import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class PlayerState:
    username: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    heading: float = 0.0
    last_update: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "heading": self.heading,
        }


class MultiplayerServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 9999):
        self.host = host
        self.port = port
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.clients: Dict[socket.socket, dict] = {}
        self.players: Dict[str, PlayerState] = {}
        self.running = False
        self.lock = threading.Lock()

    def start(self):
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(8)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self):
        self.running = False
        with self.lock:
            for sock in list(self.clients):
                try:
                    sock.close()
                except Exception:
                    pass
            self.clients.clear()
            self.players.clear()
        try:
            self.server_socket.close()
        except Exception:
            pass

    def _accept_loop(self):
        while self.running:
            try:
                client_sock, _ = self.server_socket.accept()
                client_sock.settimeout(0.2)
                with self.lock:
                    self.clients[client_sock] = {"username": None}
                threading.Thread(target=self._client_loop, args=(client_sock,), daemon=True).start()
            except OSError:
                break
            except Exception:
                continue

    def _client_loop(self, client_sock: socket.socket):
        try:
            while self.running:
                try:
                    data = client_sock.recv(4096)
                except socket.timeout:
                    continue
                except ConnectionResetError:
                    break
                if not data:
                    break
                for raw_line in data.splitlines():
                    try:
                        message = json.loads(raw_line.decode("utf-8"))
                        self._handle_message(client_sock, message)
                    except json.JSONDecodeError:
                        continue
        finally:
            self._remove_client(client_sock)

    def _handle_message(self, client_sock: socket.socket, message: dict):
        mtype = message.get("type")
        if mtype == "join":
            username = str(message.get("username", "Guest"))[:32]
            with self.lock:
                self.clients[client_sock]["username"] = username
                self.players[username] = PlayerState(username=username)
            self._broadcast_players()
        elif mtype == "state":
            with self.lock:
                username = self.clients.get(client_sock, {}).get("username")
                if not username:
                    return
                state = self.players.get(username)
                if state:
                    state.x = float(message.get("x", state.x))
                    state.y = float(message.get("y", state.y))
                    state.z = float(message.get("z", state.z))
                    state.heading = float(message.get("heading", state.heading))
                    state.last_update = time.time()
            self._broadcast_players()

    def _remove_client(self, client_sock: socket.socket):
        with self.lock:
            client_info = self.clients.pop(client_sock, None)
            if client_info:
                username = client_info.get("username")
                if username and username in self.players:
                    del self.players[username]
                try:
                    client_sock.close()
                except Exception:
                    pass
        self._broadcast_players()

    def _broadcast_players(self):
        with self.lock:
            payload = {"type": "players", "players": [p.to_dict() for p in self.players.values()]}
            raw = (json.dumps(payload) + "\n").encode("utf-8")
            for sock in list(self.clients):
                try:
                    sock.sendall(raw)
                except Exception:
                    self._remove_client(sock)


class MultiplayerClient:
    def __init__(self, server_host: str = "127.0.0.1", server_port: int = 9999, username: str = "Player"):
        self.server_host = server_host
        self.server_port = server_port
        self.username = username
        self.socket: Optional[socket.socket] = None
        self.players: Dict[str, PlayerState] = {}
        self.running = False
        self.lock = threading.Lock()

    def connect(self, timeout: float = 3.0) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((self.server_host, self.server_port))
            sock.settimeout(0.2)
            self.socket = sock
            self.running = True
            self._send({"type": "join", "username": self.username})
            threading.Thread(target=self._receive_loop, daemon=True).start()
            return True
        except Exception:
            return False

    def disconnect(self):
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
        with self.lock:
            self.players.clear()

    def _send(self, message: dict):
        if not self.socket:
            return
        try:
            raw = (json.dumps(message) + "\n").encode("utf-8")
            self.socket.sendall(raw)
        except Exception:
            self.disconnect()

    def send_state(self, x: float, y: float, z: float, heading: float):
        self._send({"type": "state", "x": x, "y": y, "z": z, "heading": heading})

    def _receive_loop(self):
        assert self.socket is not None
        while self.running:
            try:
                data = self.socket.recv(4096)
                if not data:
                    break
            except socket.timeout:
                continue
            except Exception:
                break
            for raw_line in data.splitlines():
                try:
                    message = json.loads(raw_line.decode("utf-8"))
                    self._handle_message(message)
                except json.JSONDecodeError:
                    continue
        self.disconnect()

    def _handle_message(self, message: dict):
        if message.get("type") != "players":
            return
        with self.lock:
            self.players = {}
            for player_data in message.get("players", []):
                username = player_data.get("username")
                if not username or username == self.username:
                    continue
                self.players[username] = PlayerState(
                    username=username,
                    x=float(player_data.get("x", 0.0)),
                    y=float(player_data.get("y", 0.0)),
                    z=float(player_data.get("z", 0.0)),
                    heading=float(player_data.get("heading", 0.0)),
                )

    def get_remote_players(self) -> Dict[str, PlayerState]:
        with self.lock:
            return dict(self.players)

    def get_username_overlays(self):
        with self.lock:
            return [
                {"username": state.username, "position": (state.x, state.y, state.z)}
                for state in self.players.values()
            ]


def render_username_overlays(game_renderer, overlays):
    for overlay in overlays:
        username = overlay["username"]
        x, y, z = overlay["position"]
        try:
            game_renderer.draw_text(username, x, y + 1.8, z)
        except AttributeError:
            pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Multiplayer server/client helper")
    parser.add_argument("--server", action="store_true", help="Run as server")
    parser.add_argument("--host", default="127.0.0.1", help="Server host or bind address")
    parser.add_argument("--port", type=int, default=9999, help="Server port")
    parser.add_argument("--username", default="Player", help="Client username")
    args = parser.parse_args()

    if args.server:
        server = MultiplayerServer(host=args.host, port=args.port)
        server.start()
        print(f"Server running on {args.host}:{args.port}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.stop()
    else:
        client = MultiplayerClient(server_host=args.host, server_port=args.port, username=args.username)
        if not client.connect():
            print("Unable to connect to server")
        else:
            print("Connected as", args.username)
            try:
                while client.running:
                    time.sleep(1)
            except KeyboardInterrupt:
                client.disconnect()
