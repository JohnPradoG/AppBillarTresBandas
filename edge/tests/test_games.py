"""Fase 6: partidas y jugadores guardados en el equipo."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from billar_edge import games, plays
from billar_edge.web import make_server


def _call(base, path, body=None, method="GET"):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture
def server(cfg, conn):
    srv = make_server(cfg, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_first_game_is_created_and_numbers_go_up(cfg, conn):
    g1 = games.current(conn, cfg, "mesa1")
    assert g1["number"] == 1 and (g1["player1"], g1["player2"]) == games.DEFAULT_NAMES
    assert games.current(conn, cfg, "mesa1")["id"] == g1["id"]
    g2 = games.new(conn, cfg, "mesa1", "  Carlos   Pérez ", "")
    assert g2["number"] == 2 and g2["player1"] == "Carlos Pérez" and g2["player2"] == "Jugador 2"
    assert games.get(conn, g1["id"])["ended_at"] is not None
    assert games.recent_players(conn) == ["Carlos Pérez"]


def test_state_is_validated_and_names_come_from_the_game(cfg, conn):
    g = games.new(conn, cfg, "mesa1", "Ana", "Luis")
    state = {"players": [{"name": "<b>x</b>", "score": 7, "innings": 3, "bestRun": 4},
                         {"name": "y", "score": 2, "innings": 3, "bestRun": 1}],
             "turn": 1, "run": 0, "startedAt": 1_700_000_000_000, "shotStartedAt": None, "extra": "z"}
    out = games.public(games.save_state(conn, g["id"], state))
    assert [p["name"] for p in out["players"]] == ["Ana", "Luis"] and out["players"][0]["score"] == 7
    assert "extra" not in out and out["number"] == g["number"]
    row = games.get(conn, g["id"])
    assert (row["score1"], row["score2"], row["innings"]) == (7, 2, 3)
    with pytest.raises(ValueError):
        games.save_state(conn, g["id"], {**state, "turn": 5})
    with pytest.raises(ValueError):
        games.save_state(conn, g["id"], {**state, "players": state["players"][:1]})


def test_old_game_cannot_be_overwritten(cfg, conn):
    old = games.current(conn, cfg, "mesa1")
    games.new(conn, cfg, "mesa1", "A", "B")
    with pytest.raises(games.StaleGame):
        games.save_state(conn, old["id"], {"players": [{}, {}]})


def test_rename_keeps_the_score(cfg, conn):
    g = games.new(conn, cfg, "mesa1", "A", "B")
    games.save_state(conn, g["id"], {"players": [{"score": 5}, {"score": 1}]})
    out = games.public(games.rename(conn, g["id"], "Ana", ""))
    assert [p["name"] for p in out["players"]] == ["Ana", "B"] and out["players"][0]["score"] == 5


def test_play_keeps_its_game(cfg, conn):
    g = games.new(conn, cfg, "mesa1", "A", "B")
    pid = plays.record(conn, cfg, "mesa1", 1, 0, 2, "pantalla", {"game_id": g["id"], "game_number": 1})
    assert plays.get(conn, pid)["game_id"] == g["id"]
    pid = plays.record(conn, cfg, "mesa1", 1, 0, 2, "pantalla", {"game_id": "no-existe"})
    assert plays.get(conn, pid)["game_id"] is None


def test_game_api(cfg, conn, server):
    first = _call(server, "/api/game")
    assert first["game"]["number"] == 1 and first["recent_players"] == []
    state = {**first["game"], "turn": 0}
    state["players"][0]["score"] = 3
    saved = _call(server, f"/api/game/{first['game']['id']}", {"state": state}, "PUT")
    assert saved["players"][0]["score"] == 3 and saved["turn"] == 0
    new = _call(server, "/api/game", {"player1": "Carlos", "player2": "Ana"}, "POST")
    assert new["game"]["number"] == 2 and set(new["recent_players"]) == {"Carlos", "Ana"}
    # La pantalla vieja recibe la partida vigente para ponerse al día.
    with pytest.raises(urllib.error.HTTPError) as e:
        _call(server, f"/api/game/{first['game']['id']}", {"state": state}, "PUT")
    assert e.value.code == 409 and json.loads(e.value.read())["game"]["number"] == 2
    renamed = _call(server, f"/api/game/{new['game']['id']}/players", {"player1": "Carlos", "player2": "Andrés"}, "PUT")
    assert renamed["game"]["players"][1]["name"] == "Andrés"
    with pytest.raises(urllib.error.HTTPError) as e:
        _call(server, f"/api/game/{new['game']['id']}", {"state": "x"}, "PUT")
    assert e.value.code == 400
