# Engine

Fully automatic. No approval queue. No daily check. The only human act is the bio line, and only if the token cannot write it.

This file is the law. If a prompt and this file disagree, this file wins.

## What each API is for

DeepSeek, thinking mode — the writer. It gets the book as context, the language bank, the last 40 posts, and the current weights. It writes one draft. It does not approve its own draft.

DeepSeek or Meta, instant mode — the critic. A different model from the writer. It only returns pass or fail plus the rule that failed. It does not rewrite. If it fails the draft, the writer gets one retry with the failed rule pasted in. A second fail means the slot is skipped.

Meta or DeepSeek, search mode — not a writer. Search models generalize, and generalization is the failure mode of this account. Search runs once a week and once a month, and its output is a list of scenes and a list of format facts. It never writes the caption.

Pinterest — the still. The query is the object in line one, plus night, plus no readable text, plus no face close-up. "Aesthetic", "moody", "cinematic" are banned queries. They return the same foggy street every time, which is how the grid became one card.

Audio — a non-speech bed, unique per Reel, quiet, 16–20 seconds. Room tone, rain, a buzz, a turn signal. No music with a drop. No voice. If the API can only generate speech, skip audio. Do not narrate.

Posting API — publish, then read insights at 24h and 72h. If insights fail, retry later. Do not estimate. A missing number is missing, not zero.

## Daily loop

Run once, in time to publish at 23:30 UTC or to skip cleanly before that.

1. Load weights, the language bank, the last 40 posts, and the last 14 scorecards.
2. Pick a scene. Weight by the mutation table. Do not pick a scene used in the last 40. Do not pick an object used more than twice in the last 10.
3. Writer produces one draft in the confession form. Until the win gate, the job is always confession.
4. Critic judges against the hard bans and the gold set. One retry. Then skip or pass.
5. Pinterest fetch. Reject the still if it contains baked-in text, a logo, a face larger than a third of the frame, or does not contain the object. Two fetch attempts. Then skip. Never post words over the wrong picture.
6. Render. Lines on the beat. Line one visible by 1.2s. Last line held for at least 2.5s. Total 16–20s. Slow push-in. Text in the middle two-thirds. No watermark. No handle burned in. No "comment" end card.
7. Attach the bed, or silence.
8. Publish at 23:30 UTC with the caption equal to the last line.
9. Store the row: scene id, object, verdict, hook shape, line count, pin id, audio type, post id, time.
10. If any step fails, skip the day. Log the reason. Do not fall back to a generic Reel. A skipped day is a success. A filler post is how the account stays at 100 reach.

One post per day maximum. Never catch up a missed day with two posts. Bursting looks automated and gives the model two chances to downrank you in one session.

## Scoring

Pull insights at 24h and again at 72h. Use the 72h number for decisions. Use the 24h number only as a heartbeat that the API works.

Store, per post: reach, plays, average watch time, completion, likes, comments, sends, saves, profile visits, follows.

Derived, and the only numbers the learner may use:

- sends per 1,000 reach
- hold: average watch time divided by duration
- profile visits per 1,000 reach

Likes are a tiebreaker. They are never a target. A post with likes and zero sends is a flop even if people "loved it."

Win: sends per 1,000 reach at or above 15, or reach at least 3 times the account median and at least 3 sends.

Flop: reach under 200 at 48h, or reach over 500 with zero sends.

Neither: leave the weights alone. Most posts will be neither. Do not force a verdict.

Do not score a post until 72h. The learner is not allowed to react to the first hour.

## Mutation

Every 6 scored posts, adjust. Not before.

- A hook shape or object family that won: weight times 1.2.
- A hook shape or object family that flopped: weight times 0.7.
- Never drop a family to zero before it has 8 samples. Early reach is noisy. Killing a shape on two flops is how the system chases noise.
- Never raise a family on likes alone.
- Change one variable per week once experiments are allowed. The order, if the confession is not winning: pacing first, then the object in line one, then image match. Never change voice and format in the same week. If you change both, you learn nothing, and this system has to learn without you.

If 8 of the last 10 are flops:

- Do not add a CTA. That is the move that feels like action and makes it worse.
- Do not post more often.
- Narrow the scene. More object, less abstraction.
- If the last narrowing also flopped, lengthen to 26–32 seconds as the single test, one post, then return. Only keep the longer cut if hold stays above 70% and sends rise. Otherwise the extra seconds were padding, and padding is banned again.

If 8 of the last 10 are wins: do not "scale" by posting three a day. Raise nothing except the share of the winning family. Frequency stays at one.

## Weekly search

Once a week, thinking-search, not instant.

Jobs, in this order:

1. Mine language. Look at how people with social anxiety describe the last week in their own posts — Reddit, forums, comment sections. Extract scenes, not advice. A scene has an object, an action, a body fact, and a time fact. "Practice breathing" is not a scene. "I draft the text for twenty minutes and send haha" is a scene.
2. Rewrite each scene into the house form. Never paste a stranger's post. Never use their handle. Never lift a sentence longer than a clause. Their confession is not your content. The pattern is.
3. Reject, and do not store: tips, diagnoses, medication talk, crisis or self-harm, relationship-abuse stories, anything about minors, anything that only one specific life could send.
4. Add survivors to the bank with status untested and source search. Cap new scenes at 8 per week. More than that and the search model is padding.
5. Deduplicate against the bank. Same object and same verdict means it is the same scene. Discard.

If search fails, use the bank. Do not invent slang to seem current. A stale specific scene still sends. A trendy general one does not.

## Monthly format check

Once a month, search for what Instagram is currently rewarding and suppressing: original audio, text-on-screen, unoriginal content, trial Reel rules, any change to sends versus likes.

Allowed response to a format fact:

- If a mechanic this stack can already do is being rewarded, note it. Test it as 1 post in 8, after the win gate, one variable.
- If something the system already avoids is being suppressed, do nothing. That is confirmation, not a task.
- If the only rewarded format needs a face, a trend audio, or a duet, ignore it. The stack cannot make it, and faking it will cost the account.

The monthly check is not allowed to pivot the voice.

## Trial Reels, later

When the account crosses the threshold the API reports — expect roughly 200 followers on a professional account — turn Trial Reels on for every draft.

New rule from that day: a Reel hits the grid only if the trial's sends-per-reach and hold beat the median of the last 10 grid posts. Otherwise it stays off the grid. This is the first time the system gets a free test. Use it. Do not manually "share with everyone" because a trial got likes.

Until that threshold, this section is dormant. Do not build a workaround.

## Door gate

Door posts are off while all of these are true:

- fewer than 3 wins in the last 14 days, and
- fewer than 500 followers

When either flips, door posts become 1 in 10, confession shape, one sentence that the private version is in the book, no price in the Reel, no "link in bio" spoken as a command. The price stays in the bio.

If a door post flops twice in a row, door posts turn back off for 21 days. The confession does not stop.

## DM

Trigger: they DM anything, or they comment with a confession rather than an emoji. Do not reply to emoji-only comments. Do not reply to bait.

First message, within an hour, is a page. Not a link. The page is one scene from the book, in the Reel's voice, 80–140 words, no pitch. The script bank is in PROMPTS.md. Rotate. Never send the same page twice to the same person.

Second message, 18–24 hours later, one line, the Gumroad link, no question, no "let me know." Then stop. No third message. A third message is the thing this audience mutes.

If they reply in between, answer the reply. Do not attach the link early because they were polite.

Never DM someone who did not speak first.

## Hard bans

The critic fails a draft that contains any of these:

- POV, as a label
- comment, keyword, "type the word", tag a friend, follow for part 2, link in bio
- social anxiety, as the name of their identity, in the Reel. The bio may say it. The Reel does not diagnose them.
- hidden program, RAS, reticular, inner child, vibration, alignment, healing journey
- you are not alone
- just be yourself, it gets better, you are enough, as the last line
- a tip, a step, a breathing instruction
- a question
- two scenes in one Reel
- an AI voice, a face, a burned-in handle
- a sentence lifted from the book
- medical or legal claims
- anyone under 18, school-specific cruelty, suicide, self-harm method

Also fail:

- line one has no concrete noun
- last line is not a rename
- the still does not contain the object
- duration outside 16–20 seconds, unless the single length test is the active experiment
- overlap with any of the last 40 posts above a shared object plus a shared verdict

## What "no maintenance" excludes

The system does not ping you for approvals, captions, image picks, or "should I post today."

It may stop posting. That is not a failure. If the bank is exhausted and search is down, it waits. If the token expires, it cannot invent a new token. Token expiry is the one outage you would have to see, and the log should say that in one line. Everything else retries or skips.

It does not email you a weekly report. The scorecard is for the learner, not for you. If you want to look, the rows are there. The system does not need you to look for it to change weights.

## Failure modes it must refuse

Optimizing for likes. Banned in the scoring section. Repeated here because every learner drifts toward the number that is easiest to move.

Optimizing for comments. Same problem. A keyword CTA can manufacture comments and still kill sends. Comments are not in the derived metrics on purpose.

Getting more specific until the scene is one person's Tuesday. The three-friends test in STRATEGY.md is a critic rule, not a slogan.

Getting more general until the scene is "social situations are hard." The critic fails any draft that could be said of shyness, introversion, and a busy week equally. If a non-anxious person would also nod, it is too broad to send.

Trend-jacking. A meme format that is "working" this week is working for accounts with faces and a tolerance for looking like everyone else. This account does not have that tolerance. The unoriginal-content penalty is account-level. One trend Reel can tax the next ten confession Reels.

Teaching the method. The moment the Reel becomes a lesson, the book becomes optional and the voice becomes a coach. Coaches do not get sent at midnight. Notes do.

Posting through a bad week to "stay consistent." Consistency of garbage is how a small account gets classified. The skip rule is the consistency that matters.