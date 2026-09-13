"""API level tests.

The rule tests cover the state machine. These cover the HTTP contract, and in
particular the one thing the state machine cannot enforce on its own: that no
response sent during an active round contains anything identifying the song.
"""

import pytest

from app.config.stages import CLIP_STAGES, FINAL_STAGE_INDEX


def _start_round(client, difficulty="easy", session_id=None):
    response = client.post(
        "/api/game/round",
        json={"difficulty": difficulty, "session_id": session_id},
    )
    assert response.status_code == 201
    return response.json()


def test_health_reports_providers_without_leaking_secrets(client):
    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["song_provider"] == "mock"
    # Only a boolean, never the credentials themselves.
    assert isinstance(body["spotify_configured"], bool)
    assert "spotify_client_secret" not in body


def test_config_serves_the_stage_ladder(client):
    body = client.get("/api/config").json()

    assert [stage["duration"] for stage in body["stages"]] == CLIP_STAGES
    assert body["total_stages"] == len(CLIP_STAGES)
    assert body["stages"][0]["label"] == "0.01s"
    assert len(body["difficulties"]) == 5


def test_start_round_does_not_leak_the_answer(client):
    """The rule that a network inspector must not reveal the song."""
    body = _start_round(client)
    serialized = str(body).lower()

    assert body["stage_index"] == 0
    assert body["clip_duration"] == CLIP_STAGES[0]
    assert body["status"] == "playing"

    # No identifying field, and no Audidle song id either: every catalog song
    # has one, so leaking it would reveal which results could be the answer.
    for forbidden in ("title", "artist", "album", "song", "song_id", "external_url"):
        assert forbidden not in body

    # And the audio URL is round scoped, not a filename that names the track.
    assert body["audio"]["url"] == f"/api/game/{body['round_id']}/audio"
    assert ".wav" not in serialized
    assert "mock0" not in serialized


def test_result_is_refused_while_playing(client):
    body = _start_round(client)

    response = client.get(f"/api/game/{body['round_id']}/result")

    assert response.status_code == 409
    assert response.json()["code"] == "round_still_active"


def test_unknown_round_returns_404(client):
    response = client.get("/api/game/nope/result")

    assert response.status_code == 404
    assert response.json()["code"] == "round_not_found"


def test_search_returns_guessable_results(client):
    body = client.get("/api/search", params={"q": "neon"}).json()

    assert body["results"]
    first = body["results"][0]
    assert first["external_id"]
    assert first["provider"] == "mock"
    assert first["title"]
    assert first["artist"]


def test_search_rejects_an_empty_query(client):
    assert client.get("/api/search", params={"q": ""}).status_code == 422


def test_full_losing_round_then_reveal(client):
    """Walks the whole ladder, then checks the reveal."""
    body = _start_round(client, difficulty="medium")
    round_id = body["round_id"]

    for expected_stage in range(1, FINAL_STAGE_INDEX + 1):
        state = client.post(f"/api/game/{round_id}/skip").json()
        assert state["stage_index"] == expected_stage
        assert state["clip_duration"] == CLIP_STAGES[expected_stage]
        # Still playing, even on the final stage.
        assert state["status"] == "playing"

    final = client.post(f"/api/game/{round_id}/skip").json()
    assert final["status"] == "revealing"

    result = client.get(f"/api/game/{round_id}/result").json()
    assert result["won"] is False
    assert result["outcome"] == "failed"
    assert result["stages_used"] == len(CLIP_STAGES)
    assert result["duration_reached"] == CLIP_STAGES[-1]
    assert result["song"]["title"]
    assert result["audio"] is not None


def _peek_answer(round_id: str) -> str:
    """Read a round's answer straight from the store.

    A test only move. It exists precisely because there is no legitimate way to
    learn the answer over HTTP while a round is active, which is the property
    test_start_round_does_not_leak_the_answer is asserting.
    """
    from app.dependencies import get_round_repository

    repository = get_round_repository()
    return repository._rounds[round_id].song_id


def test_winning_round_reports_stages_used(client):
    body = _start_round(client, difficulty="hard")
    round_id = body["round_id"]

    client.post(f"/api/game/{round_id}/skip")
    client.post(f"/api/game/{round_id}/skip")

    guess = client.post(
        f"/api/game/{round_id}/guess",
        json={"provider": "mock", "external_id": _peek_answer(round_id)},
    ).json()

    assert guess["correct"] is True
    assert guess["round_ended"] is True
    assert guess["round"]["status"] == "revealing"
    # Won on the third stage, so the player used three of them.
    assert guess["guessed_track"]["title"]

    result = client.get(f"/api/game/{round_id}/result").json()
    assert result["won"] is True
    assert result["stages_used"] == 3
    assert result["duration_reached"] == CLIP_STAGES[2]


def test_wrong_guess_keeps_the_same_round_and_advances_one_stage(client):
    """The API level statement of the rule that a wrong guess never rerolls."""
    body = _start_round(client)
    round_id = body["round_id"]
    answer = _peek_answer(round_id)

    first = client.post(
        f"/api/game/{round_id}/guess",
        json={"provider": "mock", "external_id": "wrong-id"},
    ).json()
    second = client.post(f"/api/game/{round_id}/skip").json()

    assert first["correct"] is False
    assert first["round"]["stage_index"] == 1
    assert second["stage_index"] == 2
    # Same song throughout, verified against the store rather than the wire.
    assert _peek_answer(round_id) == answer


def test_audio_route_serves_playable_bytes(client):
    body = _start_round(client)

    response = client.get(f"/api/game/{body['round_id']}/audio")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/")
    # A RIFF header, so the browser can actually decode this.
    assert response.content[:4] == b"RIFF"


def test_finished_round_rejects_further_guesses(client):
    body = _start_round(client)
    round_id = body["round_id"]
    for _ in range(len(CLIP_STAGES)):
        client.post(f"/api/game/{round_id}/skip")

    response = client.post(
        f"/api/game/{round_id}/guess", json={"provider": "mock", "external_id": "x"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "round_already_ended"


def test_invalid_difficulty_is_rejected(client):
    response = client.post("/api/game/round", json={"difficulty": "impossibly-hard"})

    assert response.status_code == 422


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard", "expert", "impossible"])
def test_recent_songs_are_avoided_within_a_session(client, difficulty):
    """Best effort, so this checks consecutive rounds rather than all of them."""
    session = f"test-session-{difficulty}"
    seen = []
    for _ in range(3):
        body = _start_round(client, difficulty=difficulty, session_id=session)
        client.post(f"/api/game/{body['round_id']}/abandon")
        result = client.get(f"/api/game/{body['round_id']}/result").json()
        seen.append(result["song"]["external_id"])

    assert len(set(seen)) == len(seen)
