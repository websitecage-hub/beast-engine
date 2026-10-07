"""language.py — THE SCENE BANK (LANGUAGE.md).

The writer is given ONE scene row and must keep its object. Everything the
account needs to avoid repeating itself lives here: the seed bank, the five
families the learner weights, the eligibility rules (a row rests 40 posts, an
object rests after two uses in ten), and the three-friend test that decides
whether a row may enter at all.

Why this module exists rather than a prompt: the old pipeline let the model
invent a "topic" string per reel, and it produced the same idea four times in a
week with an identical hook. Dedup has to be enforceable, so scenes are data.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------- families

FAMILIES = (
    "phone_and_messages",
    "the_body_in_the_room",
    "leaving_and_dodging",
    "the_performance_of_fine",
    "after_alone",
)

FAMILY_LABELS = {
    "phone_and_messages": "phone and messages",
    "the_body_in_the_room": "the body in the room",
    "leaving_and_dodging": "leaving and dodging",
    "the_performance_of_fine": "the performance of fine",
    "after_alone": "after, alone",
}

# A row used once is ineligible for 40 posts. An object may appear twice in ten
# posts and then must rest.
ROW_REST_POSTS = 40
OBJECT_MAX_PER_10 = 2
SEARCH_ADD_CAP_PER_WEEK = 8


# ------------------------------------------------------------------ seed scenes
# Each row: id, family, object, action, body fact, time fact, verdict.
# The object is the thing line one must contain, and it is also what the
# Pinterest query is built from — that is how the picture and the words agree.

SEED = [
    ("phone_ring_out", "phone_and_messages", "phone",
     "watched it ring out, waited ten minutes, typed 'just saw this'",
     "chest still tight", "the ten-minute wait", "rude"),
    ("late_laugh", "the_body_in_the_room", "half-second-late laugh",
     "laughed late, nodded anyway, replayed the missing word at home",
     "heat in the face", "the rest of the night", "slow"),
    ("deleted_line", "phone_and_messages", "group chat",
     "typed the line, deleted it, sent 'haha' after the moment passed",
     "thumb hovering", "until they changed the subject", "quiet"),
    ("said_im_good", "the_performance_of_fine", "'I'm good'",
     "said it all day with a smile on cue, hands started in the car",
     "hands", "the drive, not the room", "fake"),
    ("cancel_relief", "leaving_and_dodging", "cancelled plan",
     "cancelled, relief arrived before the text sent, guilt an hour later",
     "the drop when the plan existed, the ease when it didn't",
     "the hour between relief and guilt", "flaky"),
    ("invite_unread", "after_alone", "invite",
     "read it four times, wanted it, thumb won't open it",
     "thumb", "four reads, no open", "ungrateful"),
    ("aisle_turn", "leaving_and_dodging", "aisle",
     "saw them at the end of it and turned down the next one",
     "the turn happens before the thought", "the whole drive home", "cold"),
    ("voice_note", "phone_and_messages", "voice note",
     "recorded it, hated the playback, deleted it, sent a text instead",
     "the flinch at the sound of their own voice", "one listen", "awkward"),
    ("name_across_room", "the_body_in_the_room", "their name across the room",
     "heard it, stomach dropped before they knew why, smiled late",
     "stomach", "before the reason arrived", "weird"),
    ("not_much", "the_performance_of_fine", "story",
     "rehearsed it in the shower, got to the room, said 'not much'",
     "the story still sitting in the throat", "the shower before, the silence after",
     "boring"),
    ("should_hang_out", "the_performance_of_fine", "'we should hang out'",
     "said it and already knew they would not follow up",
     "the warmth was real and the follow-up was impossible",
     "the days after, watching their own silence", "fake friend"),
    ("left_on_read", "phone_and_messages", "message",
     "opened it, typed, left the app, answered hours too late",
     "the app icon becoming a threat", "hours, then a too-late reply", "rude"),
    ("group_photo", "after_alone", "photo",
     "stood at the edge and still worried they ruined the frame",
     "not knowing where to put the hands", "the second the camera came out",
     "too much"),
    ("cashier_question", "the_body_in_the_room", "cashier",
     "said the wrong short answer and replayed it in the parking lot",
     "the rush to end the exchange", "a thirty-second talk, a twenty-minute replay",
     "awkward"),
    ("party_door", "leaving_and_dodging", "last door",
     "already inside the building, could not make it, turned around",
     "feet stopping", "one door", "dramatic"),
    ("thanks_too_many", "the_performance_of_fine", "compliment",
     "didn't know what to do with their face, said thanks too many times",
     "face won't land", "the thank-yous stacking", "too much"),
    ("their_plan_cancelled", "leaving_and_dodging", "cancelled plan (theirs)",
     "felt relief when the other person cancelled, then shame at the relief",
     "the uninvited smile", "the moment the cancellation arrived", "bad friend"),
    ("practised_no", "phone_and_messages", "'sorry I can't'",
     "practised it for twenty minutes; the text was one sentence",
     "the rehearsal longer than the event", "twenty minutes for eight words",
     "ridiculous"),
    ("longer_route", "leaving_and_dodging", "longer route",
     "walked it so they would not pass someone they like",
     "the detour decided by the legs", "the extra block", "cold"),
    ("joke_in_head", "the_body_in_the_room", "joke",
     "funny in their head; out loud, 'yeah', twice",
     "the joke dying on the way out", "the gap where the joke should have been",
     "dull"),
    ("meeting_answer", "the_body_in_the_room", "meeting",
     "knew the answer, waited too long, someone else said it, nodded along",
     "throat closed on a sentence they had", "three seconds too late", "useless"),
    ("home_still_mute", "after_alone", "front door",
     "got home from being fine and could not speak to the person they live with",
     "the social battery story they use so they don't have to say fear",
     "the first ten minutes inside", "broken"),
    ("blue_ticks", "phone_and_messages", "blue ticks",
     "read it and felt the obligation of being perceived reading it",
     "the seen-receipt as a small alarm", "the minutes after being seen", "rude"),
    ("you_good", "the_performance_of_fine", "'you good?'",
     "the true answer would have taken the rest of the night; they said yeah",
     "the true answer physically too long",
     "one second to lie, hours to regret the lie", "liar"),
    ("story_didnt_hear", "the_body_in_the_room", "story",
     "laughed at it because asking again felt like a debt",
     "the laugh as a door closing", "the rest of that conversation", "fake"),
    ("stayed_too_long", "leaving_and_dodging", "exit",
     "wanted to leave, had no sentence for leaving, stayed until it was strange",
     "standing when they had already left in their head", "the extra half hour",
     "awkward"),
    ("filled_the_pause", "the_body_in_the_room", "pause",
     "filled it with a worse sentence than silence would have been",
     "panic at the gap", "one pause", "annoying"),
    ("deflected_compliment", "the_performance_of_fine", "compliment",
     "deflected it so fast the other person looked embarrassed",
     "cannot hold being seen kindly", "the instant after the compliment",
     "ungrateful"),
    ("deleted_real_message", "phone_and_messages", "draft",
     "typed a real message to someone they miss, deleted it, sent nothing",
     "the dignity was fear", "the draft that never existed for anyone else",
     "cold"),
    ("woke_in_replay", "after_alone", "morning",
     "woke up still inside yesterday's conversation",
     "the night didn't end", "before they were even upright", "obsessive"),
]

VERDICTS = ("rude", "flaky", "fake", "cold", "boring", "too much", "broken",
            "ungrateful", "dramatic", "weak", "slow", "quiet", "awkward",
            "weird", "annoying", "useless", "dull", "liar", "obsessive",
            "fake friend", "bad friend", "ridiculous", "stupid", "stuck-up")

# ------------------------------------------------------- the named referent
#
# WHY THIS EXISTS: the first live sample shipped "Aisle ends and you see them."
# — "them" with no referent. A viewer cannot picture "them". The spec's own gold
# set never does this: every line that mentions a person names them ("the invite",
# "your hands", "the words", "a name you already know"), and the two breakout
# April reels name the thing ("that dark room", "that cancelled plan").
#
# So every row now carries a SUBJECT: the concrete person or thing that line one
# can be built on. Pronouns for a person ("them", "they", "someone") are only
# allowed when the subject has already been named in the draft. That is the rule
# the gate enforces, and it is the rule the old engine had no way to state.
SUBJECT = {
    "phone_ring_out":       "your mother",
    "late_laugh":           "the table you were sitting at",
    "deleted_line":         "the group chat",
    "said_im_good":         "your manager",
    "cancel_relief":        "the friend whose plan you cancelled",
    "invite_unread":        "the invite",
    "aisle_turn":           "the old coworker",
    "voice_note":           "the friend waiting on the voice note",
    "name_across_room":     "your old flatmate",
    "not_much":             "your manager",
    "should_hang_out":      "the guy from the gym",
    "left_on_read":         "your brother",
    "group_photo":          "the group photo",
    "cashier_question":     "the cashier",
    "party_door":           "the host",
    "thanks_too_many":      "the woman who complimented you",
    "their_plan_cancelled": "the friend who cancelled",
    "practised_no":         "your cousin",
    "longer_route":         "the neighbour at the mailbox",
    "joke_in_head":         "the joke",
    "meeting_answer":       "your manager in the meeting",
    "home_still_mute":      "your partner",
    "blue_ticks":           "the message",
    "you_good":             "your brother",
    "story_didnt_hear":     "the story",
    "stayed_too_long":      "the host",
    "filled_the_pause":     "the new starter",
    "deflected_compliment": "the compliment",
    "deleted_real_message": "your oldest friend",
    "woke_in_replay":       "yesterday's conversation",
}

# The head noun of a subject, which is what a draft must actually contain for the
# referent to be on the screen. "the friend whose plan you cancelled" -> "friend".
def subject_head(subject: str) -> str:
    """The one word a draft has to contain for the referent to be named."""
    s = re.sub(r"^(your|the|a|an|that|this)\s+", "", str(subject or "").strip().lower())
    # drop any relative clause and keep the head noun
    s = re.split(r"\s+(?:whose|who|that|in|at|from|on|you)\b", s)[0].strip()
    return s

# Object families that may appear twice in ten posts and then must rest. Kept
# coarse on purpose: "phone" and "message" and "draft" are one object to a
# viewer, so counting them separately would let the grid repeat itself.
OBJECT_KEYS = {
    "phone": "phone", "message": "phone", "blue ticks": "phone",
    "draft": "phone", "voice note": "phone", "group chat": "phone",
    "'sorry I can't'": "phone",
    "compliment": "compliment", "story": "story", "pause": "pause",
    "aisle": "route", "longer route": "route", "exit": "route",
    "last door": "door", "front door": "door",
    "cancelled plan": "plan", "cancelled plan (theirs)": "plan",
    "invite": "invite", "photo": "photo", "meeting": "room",
    "their name across the room": "room", "cashier": "stranger",
    "half-second-late laugh": "laugh", "joke": "laugh",
    "'I'm good'": "fine", "'we should hang out'": "fine",
    "'you good?'": "fine", "morning": "morning",
}


def object_key(row) -> str:
    """The coarse object family a row belongs to."""
    raw = (row.get("object") if isinstance(row, dict) else row[2]) or ""
    return OBJECT_KEYS.get(str(raw).strip().lower(), str(raw).strip().lower())


def rows() -> list:
    """The seed bank as dicts, each with its named referent."""
    return [{"id": r[0], "family": r[1], "object": r[2], "action": r[3],
             "body": r[4], "time": r[5], "verdict": r[6],
             "subject": SUBJECT.get(r[0], r[2]),
             "subject_head": subject_head(SUBJECT.get(r[0], r[2])),
             "status": "seed", "source": "seed"} for r in SEED]


# Two posts in a row about the same kind of person reads as one post. A subject's
# slot is its head noun when that is a person ("friend", "manager"), otherwise
# the row's object family. An object is fine twice; the same person twice running
# is not.
PERSON_HEADS = ("friend", "manager", "brother", "partner", "cousin", "neighbour",
                "host", "cashier", "coworker", "flatmate", "starter", "mother",
                "father", "sister", "guy", "woman")


def subject_slot(row) -> str:
    head = str(row.get("subject_head") or "").strip().lower()
    if head in PERSON_HEADS:
        return head
    return object_key(row)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", str(s or "").lower()).strip()


def signature(row) -> str:
    """Dedup key: same object AND same verdict means the same scene."""
    if isinstance(row, dict):
        return f"{object_key(row)}|{_norm(str(row.get('verdict') or ''))}"
    return f"{object_key(row)}|{_norm(str(row[6]))}"


def usable(row, memory, recent_limit: int = ROW_REST_POSTS) -> tuple:
    """(ok, reason). A row may not repeat inside its rest window, its object
    family may not exceed OBJECT_MAX_PER_10 in the last ten posts, and the same
    KIND OF PERSON may not appear in two consecutive posts."""
    posts = (memory.get("posts") or [])[-recent_limit:]
    rid = row.get("id") if isinstance(row, dict) else row[0]
    for p in posts:
        if (p.get("scene_id") or "") == rid:
            return False, f"row {rid} used within the last {recent_limit} posts"

    key = object_key(row)
    recent10 = list(reversed((memory.get("posts") or [])[-10:]))
    used = sum(1 for p in recent10 if (p.get("object_key") or "") == key)
    if used >= OBJECT_MAX_PER_10:
        return False, f"object '{key}' used {used} times in the last 10 posts"

    # The named referent must rotate too. "your manager" on Monday and "your
    # manager in the meeting" on Tuesday is one post shown twice.
    slot = subject_slot(row)
    last = (memory.get("posts") or [])[-1:] or []
    if last:
        prev_slot = last[0].get("subject_slot") or ""
        if prev_slot and prev_slot == slot:
            return False, f"same referent slot '{slot}' as the previous post"
    return True, "ok"


def three_friend_test(row) -> tuple:
    """Too vague is a poster; too private is one person's Tuesday.

    A row fails vague when it carries no object or no action. It fails narrow
    when its action names something only one specific life could send — a job
    title, a city, a diagnosis, a named relationship.
    """
    obj = str(row.get("object") or "").strip()
    act = str(row.get("action") or "").strip()
    if not obj:
        return False, "no object (too vague to picture)"
    if not act:
        return False, "no action (too vague to send)"
    if len(act.split()) < 3:
        return False, "action too thin to be a scene"
    narrow = ("my boss", "my therapist", "my ex", "instagram", "tiktok",
              "diagnos", "medication", "ssri", "xanax", "adhd", "autis")
    low = f"{obj} {act}".lower()
    for bad in narrow:
        if bad in low:
            return False, f"too narrow / disallowed content: {bad}"
    return True, "ok"


def pick(memory, strategy) -> dict | None:
    """The day's scene: lowest family use, then lowest row use, then stable order.

    Families are weighted by the learner (strategy['weights']['family']), which
    starts equal. Rotation is by least-used rather than random so a 30-row bank
    with one post a day cannot repeat inside months.
    """
    weights = ((strategy or {}).get("weights") or {}).get("family") or {}
    fam_counts = {}
    for p in (memory.get("posts") or []):
        f = p.get("family")
        if f:
            fam_counts[f] = fam_counts.get(f, 0) + 1

    candidates = []
    for row in rows():
        ok, _why = usable(row, memory)
        if not ok:
            continue
        ok3, _why3 = three_friend_test(row)
        if not ok3:
            continue
        fam = row["family"]
        # Higher learner weight first, then least-used family, then least-used row.
        w = float(weights.get(fam, 1.0))
        candidates.append((-w, fam_counts.get(fam, 0), _row_uses(memory, row["id"]), row["id"], row))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[:4])
    return dict(candidates[0][4])


def _row_uses(memory, rid: str) -> int:
    return sum(1 for p in (memory.get("posts") or []) if (p.get("scene_id") or "") == rid)


def record_use(memory: dict, row: dict):
    """Stamped onto the post row so rotation and dedup can see it."""
    return {"scene_id": row["id"], "family": row["family"],
            "object": row["object"], "object_key": object_key(row),
            "verdict": row["verdict"],
            "subject": row.get("subject") or "",
            "subject_head": row.get("subject_head") or "",
            "subject_slot": subject_slot(row)}


def from_search(items: list, memory: dict) -> list:
    """Bank rows parsed from the weekly miner, capped and deduplicated.

    A mined scene enters only if it has an object, an action and a verdict, and
    only if it is not the same object-plus-verdict as a row already known.
    """
    known = {signature(r) for r in rows()}
    for p in (memory.get("posts") or []):
        if p.get("object_key") and p.get("verdict"):
            known.add(f"{p['object_key']}|{_norm(p['verdict'])}")

    out = []
    for it in (items or [])[:SEARCH_ADD_CAP_PER_WEEK]:
        row = {
            "id": f"mined_{_norm(it.get('object'))[:18]}_{_norm(it.get('verdict'))[:10]}",
            "family": it.get("family") if it.get("family") in FAMILIES else "after_alone",
            "object": it.get("object"), "action": it.get("action"),
            "body": it.get("body") or "", "time": it.get("time") or "",
            "verdict": it.get("verdict"), "status": "untested", "source": "search",
        }
        ok, _ = three_friend_test(row)
        if not ok:
            continue
        sig = signature(row)
        if sig in known:
            continue
        known.add(sig)
        out.append(row)
    return out