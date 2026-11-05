import asyncio
import json
import logging
import os
import socket
from typing import Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whatsapp_clone")

# Initialize database
db.init_db()

app = FastAPI(title="WhatsApp Mobile Messenger & Voice Calls")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
INDEX_PATH = os.path.join(STATIC_DIR, "index.html")

# Connected WebSocket clients: client_id -> {"ws": WebSocket, "username": str, "avatar": str, "in_call": bool, "invite_code": str}
connected_clients: Dict[str, Dict[str, Any]] = {}

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_live_contacts(user_id: str):
    db_contacts = db.get_user_contacts(user_id)
    result = []
    for c in db_contacts:
        cid = c["id"]
        is_online = cid in connected_clients
        in_call = connected_clients[cid].get("in_call", False) if is_online else False
        result.append({
            "id": cid,
            "username": c["username"],
            "invite_code": c["invite_code"],
            "avatar": c["avatar"],
            "is_online": is_online,
            "in_call": in_call
        })
    return result

async def notify_user(target_id: str, message: dict):
    if target_id in connected_clients:
        try:
            await connected_clients[target_id]["ws"].send_text(json.dumps(message))
            return True
        except Exception as e:
            logger.error(f"Error sending message to {target_id}: {e}")
    return False

async def refresh_user_contacts(user_id: str):
    if user_id in connected_clients:
        contacts = get_live_contacts(user_id)
        await notify_user(user_id, {
            "type": "contacts_update",
            "contacts": contacts
        })

async def refresh_contacts_for_peers(user_id: str):
    """When user_id comes online or goes offline, notify all their contacts."""
    contacts = db.get_user_contacts(user_id)
    for c in contacts:
        await refresh_user_contacts(c["id"])

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()
    logger.info(f"WebSocket connected: {client_id}")

    try:
        while True:
            data_text = await websocket.receive_text()
            data = json.loads(data_text)
            msg_type = data.get("type")

            if msg_type == "join":
                username = data.get("username", f"User_{client_id[:4]}")
                avatar = data.get("avatar", "1")
                
                # Fetch or create in SQLite DB
                user_record = db.get_or_create_user(client_id, username, avatar)
                invite_code = user_record["invite_code"]

                connected_clients[client_id] = {
                    "ws": websocket,
                    "username": username,
                    "avatar": avatar,
                    "invite_code": invite_code,
                    "in_call": False
                }
                logger.info(f"User joined: {username} [{invite_code}] ({client_id})")

                # Send welcome with profile info
                contacts = get_live_contacts(client_id)
                pending_invites = db.get_pending_invitations_for(client_id)
                call_logs = db.get_user_call_logs(client_id)

                await websocket.send_text(json.dumps({
                    "type": "welcome",
                    "userId": client_id,
                    "username": username,
                    "avatar": avatar,
                    "inviteCode": invite_code,
                    "contacts": contacts,
                    "pendingInvites": pending_invites,
                    "callLogs": call_logs
                }))

                # Notify contacts that this user is now online
                await refresh_contacts_for_peers(client_id)

            elif msg_type == "delete_account":
                db.delete_user_account(client_id)
                await refresh_contacts_for_peers(client_id)
                connected_clients.pop(client_id, None)
                await websocket.send_text(json.dumps({
                    "type": "account_deleted",
                    "success": True
                }))
                return

            elif msg_type == "update_profile":
                new_username = data.get("username", "").strip()
                new_avatar = data.get("avatar", "1")
                if new_username:
                    db.update_user_profile(client_id, new_username, new_avatar)
                    if client_id in connected_clients:
                        connected_clients[client_id]["username"] = new_username
                        connected_clients[client_id]["avatar"] = new_avatar
                    await refresh_contacts_for_peers(client_id)
                    await websocket.send_text(json.dumps({
                        "type": "profile_updated",
                        "username": new_username,
                        "avatar": new_avatar
                    }))

            elif msg_type == "send_invitation":
                # Invite by code
                target_code = data.get("targetCode", "").strip()
                target_user = db.get_user_by_code(target_code)

                if not target_user:
                    await websocket.send_text(json.dumps({
                        "type": "invite_result",
                        "success": False,
                        "message": f"No user found with Chat ID: {target_code}"
                    }))
                elif target_user["id"] == client_id:
                    await websocket.send_text(json.dumps({
                        "type": "invite_result",
                        "success": False,
                        "message": "You cannot invite yourself!"
                    }))
                else:
                    res = db.create_invitation(client_id, target_user["id"])
                    if "error" in res:
                        await websocket.send_text(json.dumps({
                            "type": "invite_result",
                            "success": False,
                            "message": res["error"]
                        }))
                    else:
                        sender_name = connected_clients.get(client_id, {}).get("username", "Someone")
                        sender_avatar = connected_clients.get(client_id, {}).get("avatar", "1")
                        sender_code = connected_clients.get(client_id, {}).get("invite_code", "")

                        await websocket.send_text(json.dumps({
                            "type": "invite_result",
                            "success": True,
                            "message": f"Invitation sent to {target_user['username']}! Waiting for acceptance."
                        }))

                        # Real-time alert to recipient if online
                        await notify_user(target_user["id"], {
                            "type": "new_invitation",
                            "invitationId": res["invitation_id"],
                            "fromId": client_id,
                            "fromName": sender_name,
                            "fromCode": sender_code,
                            "fromAvatar": sender_avatar
                        })

            elif msg_type == "accept_invitation":
                invitation_id = data.get("invitationId")
                res = db.accept_invitation(invitation_id, client_id)
                if res.get("success"):
                    other_user_id = res["contact_id"]
                    my_name = connected_clients.get(client_id, {}).get("username", "Your contact")

                    # Refresh contacts on both sides
                    await refresh_user_contacts(client_id)
                    await refresh_user_contacts(other_user_id)

                    # Notify sender that invite was accepted
                    await notify_user(other_user_id, {
                        "type": "invitation_accepted",
                        "byName": my_name,
                        "byId": client_id
                    })

                    # Send updated pending list to acceptor
                    pending_invites = db.get_pending_invitations_for(client_id)
                    await websocket.send_text(json.dumps({
                        "type": "pending_invites_update",
                        "invites": pending_invites
                    }))

            elif msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif msg_type == "reject_invitation":
                invitation_id = data.get("invitationId")
                db.reject_invitation(invitation_id, client_id)
                pending_invites = db.get_pending_invitations_for(client_id)
                await websocket.send_text(json.dumps({
                    "type": "pending_invites_update",
                    "invites": pending_invites
                }))

            elif msg_type == "get_history":
                target_id = data.get("targetId")
                history = db.get_chat_history(client_id, target_id)
                await websocket.send_text(json.dumps({
                    "type": "chat_history",
                    "targetId": target_id,
                    "messages": history
                }))

            elif msg_type == "chat_message":
                target_id = data.get("targetId")
                text = data.get("text", "")
                time_str = data.get("time", "")
                msg_id = data.get("id") or f"msg_{os.urandom(6).hex()}"

                # Save to database
                db.save_message(msg_id, client_id, target_id, text, time_str)

                sender_name = connected_clients.get(client_id, {}).get("username", "Unknown")
                sender_avatar = connected_clients.get(client_id, {}).get("avatar", "1")

                out_payload = {
                    "type": "chat_message",
                    "id": msg_id,
                    "senderId": client_id,
                    "senderName": sender_name,
                    "senderAvatar": sender_avatar,
                    "targetId": target_id,
                    "text": text,
                    "time": time_str
                }

                # Forward to recipient
                delivered = await notify_user(target_id, out_payload)

                # Return receipt to sender
                out_payload["isSender"] = True
                out_payload["delivered"] = delivered
                await websocket.send_text(json.dumps(out_payload))

            elif msg_type == "typing":
                target_id = data.get("targetId")
                sender_name = connected_clients.get(client_id, {}).get("username", "Unknown")
                await notify_user(target_id, {
                    "type": "typing",
                    "senderId": client_id,
                    "senderName": sender_name,
                    "isTyping": data.get("isTyping", False)
                })

            # --- WebRTC Voice Call Signaling ---
            elif msg_type == "call_offer":
                target_id = data.get("targetId")
                caller_info = connected_clients.get(client_id, {})
                caller_name = caller_info.get("username", "Caller")
                caller_avatar = caller_info.get("avatar", "1")

                if target_id in connected_clients:
                    callee = connected_clients[target_id]
                    if callee.get("in_call"):
                        await websocket.send_text(json.dumps({
                            "type": "call_busy",
                            "reason": f"{callee.get('username')} is on another call."
                        }))
                    else:
                        connected_clients[client_id]["in_call"] = True
                        await notify_user(target_id, {
                            "type": "call_offer",
                            "callerId": client_id,
                            "callerName": caller_name,
                            "callerAvatar": caller_avatar,
                            "sdp": data.get("sdp")
                        })
                else:
                    # Save missed call in DB
                    db.save_call_log(
                        f"call_{os.urandom(4).hex()}",
                        client_id, target_id, "missed", 0, data.get("time", "")
                    )
                    await websocket.send_text(json.dumps({
                        "type": "call_unavailable",
                        "message": "User is currently offline."
                    }))

            elif msg_type == "call_answer":
                target_id = data.get("targetId")
                if client_id in connected_clients:
                    connected_clients[client_id]["in_call"] = True
                await notify_user(target_id, {
                    "type": "call_answer",
                    "responderId": client_id,
                    "sdp": data.get("sdp")
                })
                await refresh_contacts_for_peers(client_id)
                await refresh_contacts_for_peers(target_id)

            elif msg_type == "ice_candidate":
                target_id = data.get("targetId")
                await notify_user(target_id, {
                    "type": "ice_candidate",
                    "senderId": client_id,
                    "candidate": data.get("candidate")
                })

            elif msg_type == "call_reject":
                target_id = data.get("targetId")
                if client_id in connected_clients:
                    connected_clients[client_id]["in_call"] = False
                if target_id in connected_clients:
                    connected_clients[target_id]["in_call"] = False

                db.save_call_log(
                    f"call_{os.urandom(4).hex()}",
                    target_id, client_id, "rejected", 0, data.get("time", "")
                )
                await notify_user(target_id, {
                    "type": "call_reject",
                    "reason": data.get("reason", "Call declined")
                })
                await refresh_contacts_for_peers(client_id)
                await refresh_contacts_for_peers(target_id)

            elif msg_type == "call_end":
                target_id = data.get("targetId")
                duration = data.get("duration", 0)
                time_str = data.get("time", "")

                if client_id in connected_clients:
                    connected_clients[client_id]["in_call"] = False
                if target_id in connected_clients:
                    connected_clients[target_id]["in_call"] = False

                db.save_call_log(
                    f"call_{os.urandom(4).hex()}",
                    client_id, target_id, "answered", duration, time_str
                )
                await notify_user(target_id, {
                    "type": "call_end",
                    "duration": duration
                })
                await refresh_contacts_for_peers(client_id)
                await refresh_contacts_for_peers(target_id)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {client_id}")
    except Exception as e:
        logger.error(f"WebSocket error for {client_id}: {e}")
    finally:
        connected_clients.pop(client_id, None)
        await refresh_contacts_for_peers(client_id)

# Serve static directory & service worker safely
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

APK_PATH = os.path.join(BASE_DIR, "Chats.apk")
if not os.path.exists(APK_PATH):
    APK_PATH = os.path.join(BASE_DIR, "WhatsApp.apk")

@app.get("/download")
async def download_apk():
    if os.path.exists(APK_PATH):
        return FileResponse(
            APK_PATH,
            filename="Chats.apk",
            media_type="application/vnd.android.package-archive"
        )
    return {"error": "APK not found"}

@app.get("/manifest.json")
async def get_manifest():
    manifest_file = os.path.join(STATIC_DIR, "manifest.json")
    if os.path.exists(manifest_file):
        return FileResponse(manifest_file, media_type="application/manifest+json")
    return {"status": "ok"}

@app.get("/sw.js")
async def get_sw():
    sw_file = os.path.join(STATIC_DIR, "sw.js")
    if os.path.exists(sw_file):
        return FileResponse(sw_file, media_type="application/javascript")
    return {"status": "ok"}

@app.get("/")
async def get_index():
    if os.path.exists(INDEX_PATH):
        return FileResponse(INDEX_PATH)
    return {
        "status": "online",
        "service": "Chats Mobile Cloud Server",
        "websocket": "/ws/{client_id}"
    }

if __name__ == "__main__":
    local_ip = get_local_ip()
    port = int(os.environ.get("PORT", 8000))
    print("\n" + "="*60)
    print(" [*] Chats Mobile Server is Running!")
    print(f" [*] Local:   http://localhost:{port}")
    print(f" [*] Network: http://{local_ip}:{port}")
    print("="*60 + "\n")
    uvicorn.run("server:app", host="0.0.0.0", port=port)
