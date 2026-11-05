import sqlite3
import random
import string
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatsapp.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        invite_code TEXT UNIQUE NOT NULL,
        avatar TEXT DEFAULT '1',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS invitations (
        id TEXT PRIMARY KEY,
        from_id TEXT NOT NULL,
        to_id TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (from_id) REFERENCES users(id),
        FOREIGN KEY (to_id) REFERENCES users(id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS contacts (
        user_id TEXT NOT NULL,
        contact_id TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, contact_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (contact_id) REFERENCES users(id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        sender_id TEXT NOT NULL,
        recipient_id TEXT NOT NULL,
        text TEXT NOT NULL,
        time TEXT NOT NULL,
        status TEXT DEFAULT 'sent',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    try:
        c.execute("ALTER TABLE messages ADD COLUMN status TEXT DEFAULT 'sent'")
    except Exception:
        pass

    c.execute("""
    CREATE TABLE IF NOT EXISTS call_logs (
        id TEXT PRIMARY KEY,
        caller_id TEXT NOT NULL,
        callee_id TEXT NOT NULL,
        status TEXT NOT NULL,
        duration INTEGER DEFAULT 0,
        time TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

def generate_invite_code():
    chars = string.digits
    while True:
        code = f"CH-{''.join(random.choices(chars, k=5))}"
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE invite_code = ?", (code,))
        exists = c.fetchone()
        conn.close()
        if not exists:
            return code

def delete_user_account(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM contacts WHERE user_id = ? OR contact_id = ?", (user_id, user_id))
    c.execute("DELETE FROM invitations WHERE from_id = ? OR to_id = ?", (user_id, user_id))
    c.execute("DELETE FROM messages WHERE sender_id = ? OR recipient_id = ?", (user_id, user_id))
    c.execute("DELETE FROM call_logs WHERE caller_id = ? OR callee_id = ?", (user_id, user_id))
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"success": True}

def update_user_profile(user_id, username, avatar):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET username = ?, avatar = ? WHERE id = ?", (username, avatar, user_id))
    conn.commit()
    conn.close()
    return {"success": True}

def get_or_create_user(user_id, username, avatar):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    if row:
        c.execute("UPDATE users SET username = ?, avatar = ? WHERE id = ?", (username, avatar, user_id))
        conn.commit()
        invite_code = row["invite_code"]
    else:
        invite_code = generate_invite_code()
        c.execute("INSERT INTO users (id, username, invite_code, avatar) VALUES (?, ?, ?, ?)",
                  (user_id, username, invite_code, avatar))
        conn.commit()
    conn.close()
    return {"id": user_id, "username": username, "invite_code": invite_code, "avatar": avatar}

def get_user_by_code_or_name(query):
    if not query:
        return None
    q = query.strip()
    q_clean = q.upper().replace(" ", "").replace("#", "")
    conn = get_connection()
    c = conn.cursor()

    # 1. Exact invite code match
    c.execute("SELECT id, username, invite_code, avatar FROM users WHERE UPPER(invite_code) = ?", (q_clean,))
    row = c.fetchone()

    # 2. Match without or with 'CH-' prefix
    if not row and not q_clean.startswith("CH-"):
        c.execute("SELECT id, username, invite_code, avatar FROM users WHERE UPPER(invite_code) = ?", (f"CH-{q_clean}",))
        row = c.fetchone()
    if not row and q_clean.startswith("CH-"):
        without_prefix = q_clean[3:]
        c.execute("SELECT id, username, invite_code, avatar FROM users WHERE UPPER(invite_code) = ?", (without_prefix,))
        row = c.fetchone()

    # 3. Exact username match
    if not row:
        c.execute("SELECT id, username, invite_code, avatar FROM users WHERE UPPER(username) = UPPER(?)", (q,))
        row = c.fetchone()

    # 4. Partial username match
    if not row:
        c.execute("SELECT id, username, invite_code, avatar FROM users WHERE UPPER(username) LIKE UPPER(?)", (f"%{q}%",))
        row = c.fetchone()

    conn.close()
    return dict(row) if row else None

def get_user_by_code(invite_code):
    return get_user_by_code_or_name(invite_code)

def add_mutual_contacts(user_a, user_b):
    if user_a == user_b:
        return
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO contacts (user_id, contact_id) VALUES (?, ?)", (user_a, user_b))
    c.execute("INSERT OR IGNORE INTO contacts (user_id, contact_id) VALUES (?, ?)", (user_b, user_a))
    # Also resolve any invitations between them
    c.execute("UPDATE invitations SET status = 'accepted' WHERE (from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?)",
              (user_a, user_b, user_b, user_a))
    conn.commit()
    conn.close()

def resolve_pending_invitations(user_a, user_b):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE invitations SET status = 'accepted' WHERE (from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?)",
              (user_a, user_b, user_b, user_a))
    conn.commit()
    conn.close()

def get_user_by_id(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, invite_code, avatar FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_contacts(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT u.id, u.username, u.invite_code, u.avatar
        FROM contacts ct
        JOIN users u ON ct.contact_id = u.id
        WHERE ct.user_id = ?
        ORDER BY ct.created_at DESC
    """, (user_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_pending_invitations_for(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT i.id, i.from_id, u.username AS from_name, u.invite_code AS from_code, u.avatar AS from_avatar, i.created_at
        FROM invitations i
        JOIN users u ON i.from_id = u.id
        WHERE i.to_id = ? AND i.status = 'pending'
        ORDER BY i.created_at DESC
    """, (user_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def create_invitation(from_id, to_id):
    if from_id == to_id:
        return {"error": "Cannot invite yourself"}
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT 1 FROM contacts WHERE user_id = ? AND contact_id = ?", (from_id, to_id))
    if c.fetchone():
        conn.close()
        return {"error": "Already in your contacts list"}

    # Reciprocal pending invitations mean both people have requested a connection.
    c.execute("SELECT id FROM invitations WHERE from_id = ? AND to_id = ? AND status = 'pending'", (to_id, from_id))
    reverse_invite = c.fetchone()
    if reverse_invite:
        c.execute("UPDATE invitations SET status = 'accepted' WHERE (from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?)",
                  (from_id, to_id, to_id, from_id))
        c.execute("INSERT OR IGNORE INTO contacts (user_id, contact_id) VALUES (?, ?)", (from_id, to_id))
        c.execute("INSERT OR IGNORE INTO contacts (user_id, contact_id) VALUES (?, ?)", (to_id, from_id))
        conn.commit()
        conn.close()
        return {"success": True, "connected": True, "invitation_id": reverse_invite["id"]}

    c.execute("SELECT id FROM invitations WHERE from_id = ? AND to_id = ? AND status = 'pending'", (from_id, to_id))
    if c.fetchone():
        conn.close()
        return {"error": "Invitation already sent and pending"}

    inv_id = f"inv_{os.urandom(8).hex()}"
    c.execute("INSERT INTO invitations (id, from_id, to_id, status) VALUES (?, ?, ?, 'pending')", (inv_id, from_id, to_id))
    conn.commit()
    conn.close()
    return {"success": True, "connected": False, "invitation_id": inv_id}
def accept_invitation(invitation_id, user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM invitations WHERE id = ? AND to_id = ? AND status = 'pending'", (invitation_id, user_id))
    inv = c.fetchone()
    if not inv:
        conn.close()
        return {"error": "Invitation not found or already processed"}

    from_id = inv["from_id"]
    to_id = inv["to_id"]

    # Mark invitation accepted
    c.execute("UPDATE invitations SET status = 'accepted' WHERE id = ?", (invitation_id,))

    # Add mutual contacts
    c.execute("INSERT OR IGNORE INTO contacts (user_id, contact_id) VALUES (?, ?)", (from_id, to_id))
    c.execute("INSERT OR IGNORE INTO contacts (user_id, contact_id) VALUES (?, ?)", (to_id, from_id))

    conn.commit()
    conn.close()
    return {"success": True, "contact_id": from_id}

def reject_invitation(invitation_id, user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE invitations SET status = 'rejected' WHERE id = ? AND to_id = ?", (invitation_id, user_id))
    conn.commit()
    conn.close()
    return {"success": True}

def save_message(msg_id, sender_id, recipient_id, text, time_str, status='sent'):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO messages (id, sender_id, recipient_id, text, time, status) VALUES (?, ?, ?, ?, ?, ?)",
              (msg_id, sender_id, recipient_id, text, time_str, status))
    conn.commit()
    conn.close()

def update_message_status(msg_id, status):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE messages SET status = ? WHERE id = ?", (status, msg_id))
    conn.commit()
    conn.close()

def mark_messages_read(user_id, contact_id):
    """Mark all messages sent by contact_id to user_id as read"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id FROM messages 
        WHERE recipient_id = ? AND sender_id = ? AND status != 'read'
    """, (user_id, contact_id))
    rows = c.fetchall()
    updated_ids = [r["id"] for r in rows]
    if updated_ids:
        c.execute("""
            UPDATE messages SET status = 'read'
            WHERE recipient_id = ? AND sender_id = ?
        """, (user_id, contact_id))
        conn.commit()
    conn.close()
    return updated_ids

def get_chat_history(user_a, user_b, limit=50):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, sender_id, recipient_id, text, time, status
        FROM messages
        WHERE (sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?)
        ORDER BY created_at ASC
        LIMIT ?
    """, (user_a, user_b, user_b, user_a, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_call_log(call_id, caller_id, callee_id, status, duration, time_str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO call_logs (id, caller_id, callee_id, status, duration, time)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (call_id, caller_id, callee_id, status, duration, time_str))
    conn.commit()
    conn.close()

def get_user_call_logs(user_id, limit=30):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT cl.id, cl.caller_id, cl.callee_id, cl.status, cl.duration, cl.time,
               u_caller.username AS caller_name, u_caller.avatar AS caller_avatar,
               u_callee.username AS callee_name, u_callee.avatar AS callee_avatar
        FROM call_logs cl
        JOIN users u_caller ON cl.caller_id = u_caller.id
        JOIN users u_callee ON cl.callee_id = u_callee.id
        WHERE cl.caller_id = ? OR cl.callee_id = ?
        ORDER BY cl.created_at DESC
        LIMIT ?
    """, (user_id, user_id, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]
