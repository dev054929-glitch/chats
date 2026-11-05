import os
import uvicorn
from server import app as fastapi_app

try:
    import gradio as gr
    with gr.Blocks(title="Chats Server") as demo:
        gr.Markdown("# 🟢 Chats Mobile Messenger Server")
        gr.Markdown("Real-time Chat, Invitations & WebRTC Voice Calling Server is Running 24/7.")
        gr.Markdown("Download Android App directly from [/download](/download)")

    app = gr.mount_gradio_app(fastapi_app, demo, path="/status")
except Exception as e:
    app = fastapi_app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    print(f"[*] Starting Chats Cloud Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
