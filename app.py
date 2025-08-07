"""
Anonymous room-based chat app (single-file)

Requirements:
  pip install fastapi uvicorn

Run:
  uvicorn anonymous_chat:app --reload

Open in browser: http://127.0.0.1:8000/   (append ?room=roomname to join a room or use the UI to create one)

This is intentionally minimalistic and anonymous: no accounts, no private messages.
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import secrets
import json
from collections import defaultdict

app = FastAPI()

# In-memory room manager. For a production app you'd want persistent storage and better resource controls.
rooms = defaultdict(dict)  # room_name -> {ws: {nick, color}}
rooms_lock = asyncio.Lock()

HTML = r'''<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Anonymous Rooms</title>
  <style>
    :root{--bg:#0f1724;--card:#0b1220;--muted:#9aa4b2;--accent:#7dd3fc}
    *{box-sizing:border-box}
    body{margin:0;min-height:100vh;background:linear-gradient(180deg,var(--bg),#071020);font-family:Inter,Segoe UI,Roboto,Arial;color:#e6eef6}
    .wrap{max-width:900px;margin:36px auto;padding:18px}
    .card{background:linear-gradient(180deg,rgba(255,255,255,0.02),transparent);border-radius:12px;padding:14px;box-shadow:0 6px 18px rgba(2,6,23,0.6)}
    header{display:flex;gap:12px;align-items:center;justify-content:space-between}
    h1{font-size:18px;margin:0}
    .rooms{display:flex;gap:8px;align-items:center}
    input[type=text], input[type=search]{background:transparent;border:1px solid rgba(255,255,255,0.06);padding:8px 10px;border-radius:8px;color:inherit}
    button{background:var(--accent);border:none;padding:8px 12px;border-radius:8px;color:#002;cursor:pointer;font-weight:600}
    .layout{display:grid;grid-template-columns:1fr 2fr;gap:12px;margin-top:12px}
    .sidebar{padding:12px}
    .room-list{display:flex;flex-direction:column;gap:8px}
    .room-item{padding:8px;border-radius:8px;background:rgba(255,255,255,0.02);cursor:pointer}
    .chat{display:flex;flex-direction:column;height:70vh}
    .messages{flex:1;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:10px}
    .composer{display:flex;gap:8px;padding:8px;border-top:1px solid rgba(255,255,255,0.03)}
    .composer input{flex:1;padding:10px;border-radius:8px;border:1px solid rgba(255,255,255,0.04);background:transparent;color:inherit}
    .msg{display:flex;gap:10px;align-items:flex-start}
    .avatar{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-weight:700}
    .bubble{max-width:80%;padding:10px;border-radius:10px;background:rgba(255,255,255,0.03);}
    .meta{font-size:12px;color:var(--muted)}
    .note{font-size:13px;color:var(--muted);margin:8px 0}
    @media(max-width:880px){.layout{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <header>
        <h1>Anonymous Rooms — Minimal Chat</h1>
        <div class="rooms">
          <input id="roomInput" type="search" placeholder="room name (press Join)" />
          <button id="joinBtn">Join</button>
        </div>
      </header>

      <div class="layout">
        <aside class="sidebar">
          <div class="note">Tip: open the same URL in multiple tabs to test. Rooms are ephemeral and anonymous.</div>
          <div class="room-list card" id="roomList"></div>
        </aside>

        <main class="chat card">
          <div style="padding:8px;display:flex;justify-content:space-between;align-items:center">
            <div>
              <strong id="roomName">Not connected</strong>
              <div class="meta" id="userMeta"></div>
            </div>
            <div class="meta">No login • Minimal • Anonymous</div>
          </div>

          <div class="messages" id="messages"></div>

          <div class="composer">
            <input id="msgInput" placeholder="Say something..." autocomplete="off" />
            <button id="sendBtn">Send</button>
          </div>
        </main>
      </div>
    </div>
  </div>

<script>
(() => {
  const params = new URLSearchParams(location.search);
  const defaultRoom = params.get('room') || '';
  const roomInput = document.getElementById('roomInput');
  const joinBtn = document.getElementById('joinBtn');
  const roomList = document.getElementById('roomList');
  const roomName = document.getElementById('roomName');
  const userMeta = document.getElementById('userMeta');
  const messages = document.getElementById('messages');
  const msgInput = document.getElementById('msgInput');
  const sendBtn = document.getElementById('sendBtn');

  let ws = null;
  let me = null;
  const roomsSeen = new Set();

  function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  function pushMsg(el){ messages.appendChild(el); messages.scrollTop = messages.scrollHeight; }

  function makeBubble(payload){
    const wrap = document.createElement('div'); wrap.className='msg';
    const avatar = document.createElement('div'); avatar.className='avatar'; avatar.textContent = payload.nick.slice(0,2).toUpperCase();
    avatar.style.background = payload.color;
    const box = document.createElement('div');
    const header = document.createElement('div'); header.className='meta'; header.textContent = payload.nick + (payload.system? ' • '+payload.system : '');
    const bubble = document.createElement('div'); bubble.className='bubble'; bubble.innerHTML = esc(payload.text);
    box.appendChild(header); box.appendChild(bubble);
    wrap.appendChild(avatar); wrap.appendChild(box);
    return wrap;
  }

  function setRoom(r){
    roomName.textContent = r ? r : 'Not connected';
    roomInput.value = r;
    if(r && !roomsSeen.has(r)){
      const item = document.createElement('div'); item.className='room-item'; item.textContent = r; item.onclick = ()=> setRoom(r) || joinRoom(r);
      roomList.appendChild(item); roomsSeen.add(r);
    }
  }

  async function joinRoom(r){
    if(ws){ ws.close(); ws=null; }
    messages.innerHTML='';
    setRoom(r);
    const loc = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws?room=' + encodeURIComponent(r);
    ws = new WebSocket(loc);
    ws.addEventListener('open', ()=>{ console.log('ws open'); });
    ws.addEventListener('message', ev=>{
      try{ const payload = JSON.parse(ev.data);
        if(payload.type === 'meta'){ me = payload.you; userMeta.textContent = 'You: ' + me.nick; }
        if(payload.type === 'rooms'){ updateRooms(payload.rooms); }
        if(payload.type === 'msg' || payload.type === 'system'){ pushMsg(makeBubble(payload)); }
      }catch(e){ console.error(e); }
    });
    ws.addEventListener('close', ()=>{ pushMsg(makeBubble({nick:'System', text:'Disconnected.', color:'#333', system:'status'})); });
  }

  function updateRooms(list){ roomList.innerHTML=''; list.forEach(r=>{ const item=document.createElement('div'); item.className='room-item'; item.textContent=r; item.onclick=()=>joinRoom(r); roomList.appendChild(item); }); }

  joinBtn.onclick = ()=>{ const r = roomInput.value.trim(); if(!r) return alert('Choose a room name'); joinRoom(r); history.replaceState(null,'', '?room='+encodeURIComponent(r)); };
  sendBtn.onclick = ()=>{ const t = msgInput.value.trim(); if(!t) return; if(!ws || ws.readyState !== WebSocket.OPEN) return alert('Not connected'); ws.send(JSON.stringify({type:'msg', text:t})); msgInput.value=''; };
  msgInput.addEventListener('keydown', e=>{ if(e.key==='Enter') sendBtn.click(); });

  // auto-join default room if provided via URL
  if(defaultRoom) setTimeout(()=>joinBtn.click(), 100);

  // fetch list of active rooms
  fetch('/rooms').then(r=>r.json()).then(data=>{ updateRooms(data.rooms); if(defaultRoom) joinRoom(defaultRoom); });
})();
</script>
</body>
</html>
'''

async def broadcast(room: str, message: dict):
    """Send message JSON to all connections in the room."""
    to_remove = []
    async with rooms_lock:
        conns = list(rooms[room].keys())
    for ws in conns:
        try:
            await ws.send_text(json.dumps(message))
        except Exception:
            # mark for removal
            to_remove.append(ws)
    if to_remove:
        async with rooms_lock:
            for ws in to_remove:
                if ws in rooms[room]:
                    del rooms[room][ws]


@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    return HTML


@app.get('/rooms')
async def list_rooms():
    async with rooms_lock:
        # only list rooms with at least 1 connection
        active = [r for r, m in rooms.items() if len(m) > 0]
    return {"rooms": active}


@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    # join by query param: /ws?room=roomname
    await websocket.accept()
    q = websocket.query_params
    room = q.get('room', 'lobby') or 'lobby'

    # create anonymous identity
    nick = f"Anon-{secrets.token_hex(2)}"
    color = f"hsl({secrets.randbelow(360)} 70% 55%)"
    info = {'nick': nick, 'color': color}

    # register
    async with rooms_lock:
        rooms[room][websocket] = info

    # send metadata to this client
    await websocket.send_text(json.dumps({'type': 'meta', 'you': info}))
    # notify room of join
    await broadcast(room, {'type': 'system', 'text': f"{nick} joined.", 'nick': 'System', 'color': '#666', 'system': 'join'})
    # update room list for all clients
    async with rooms_lock:
        room_list = [r for r, m in rooms.items() if len(m) > 0]
    await broadcast(room, {'type': 'rooms', 'rooms': room_list})

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except Exception:
                payload = {'type': 'msg', 'text': data}

            if payload.get('type') == 'msg':
                text = payload.get('text', '')[:2000]
                sender = rooms[room].get(websocket, info)
                msg = {'type': 'msg', 'text': text, 'nick': sender['nick'], 'color': sender['color']}
                await broadcast(room, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        # cleanup
        async with rooms_lock:
            if websocket in rooms[room]:
                left = rooms[room][websocket]['nick']
                del rooms[room][websocket]
            else:
                left = 'Someone'
            if len(rooms[room]) == 0:
                try:
                    del rooms[room]
                except KeyError:
                    pass
        await broadcast(room, {'type': 'system', 'text': f"{left} left.", 'nick': 'System', 'color': '#666', 'system': 'leave'})
        async with rooms_lock:
            room_list = [r for r, m in rooms.items() if len(m) > 0]
        # broadcast updated rooms to remaining members in the room
        if room in rooms:
            await broadcast(room, {'type': 'rooms', 'rooms': room_list})


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app:app', host='0.0.0.0', port=8000, reload=True)
