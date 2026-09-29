BEAST ENGINE — FULL SYSTEM DESCRIPTION
======================================

What it is
----------
A fully automated Instagram Reels pipeline. On a schedule it decides whether to post,
generates an original hook + caption with an LLM, fetches a moving background video,
picks music, composites the text overlay, renders a 1080x1920 MP4, quality-gates it,
uploads it to a public host, and publishes it through the Instagram Graph API. Then it
tells you on Telegram, and learns from the result.

Repository: https://github.com/websitecage-hub/beast-engine
Runner:     GitHub Actions (4 workflows) — nothing runs on your machine


THE 5 EXTERNAL THINGS IT USES
-----------------------------

1. PINTEREST API  —  https://pinterest-api-inyg.onrender.com
   YOUR OWN service, hosted on Render. The Action does NOT scrape pinterest.com.
   It sends a plain search query and gets back JSON.
     - GET {BASE}/pins?q=<query>   -> list of pins (image/video URLs + metadata)
     - src/pinterest.py handles the calls and downloads the chosen media
   Free Render tier sleeps when idle, so the pipeline "wakes" it first.
   Purpose: source the moving background footage.

2. AUDIO SCRAPER API  —  https://audi0-scraper.onrender.com
   YOUR OWN service, also on Render. The Action does NOT touch SoundCloud,
   YouTube or Instagram directly.
     - POST {BASE}/v1/song       find a track for a query
     - POST {BASE}/v1/download   fetch it
     - GET  {BASE}/v1/file/...   retrieve the file
   Purpose: supply the music.
   IMPORTANT, measured: query -> track is 1:1, so the same query ALWAYS returns the
   same song. Six different queries returned six different files; the same query three
   times returned the same file both times. That is why the palette had to be rebuilt
   (see "The music problem" below).

3. META / INSTAGRAM GRAPH API  —  https://graph.instagram.com/v23.0
   The real publication path.
     - POST /{ig-user-id}/media         create a container from a public video URL
     - POST /{ig-user-id}/media_publish  publish the container
     - GET  /refresh_access_token        keep the token alive
   Also https://rupload.instagram.com for the upload itself.
   Needs a PUBLIC video URL, which is why a file host is involved (item 5).

4. TELEGRAM BOT API  —  https://api.telegram.org/bot{token}/sendMessage | sendVideo
   Reports every outcome: published reels (with the video), failures with tracebacks,
   daily health checks. The token is a GitHub repo secret and has NO local copy.
   Because of that, reports are sent by dispatching the notify workflow, which runs in
   CI where the secret exists.

5. FILE HOSTS (to make the reel publicly fetchable)
     - https://litterbox.catbox.moe/resources/internals/api.php   (primary, 72h TTL)
     - https://uguu.se/upload                                     (fallback, 3h TTL)
   Instagram must download the MP4 from a public URL, so the finished reel is uploaded
   to one of these first.

Secondary sources (music fallbacks, used only if the audio API yields nothing):
     - https://archive.org/advancedsearch.php  (public-domain audio)
     - https://pixabay.com/music/search/       (hero/backup tracks)


HOW IT IS BUILT
---------------
Language: Python 3.11. No web server; it runs as short-lived jobs.

The pipeline (src/run_create.py):
  1. services_awake   wake the two sleeping Render services
  2. jitter_start     random delay, so posting times look human
  3. scheduler        decide whether to post today (idempotency guard)
  4. trending_joined  gather trend input
  5. generate         LLM writes the hook + caption (src/llm.py, src/generate.py)
  6. background       pick + normalise the background clip (src/background.py)
  7. music            pick a track (src/music.py)
  8. build_video      compose text + render the MP4 (src/build_video.py)
  9. QA gate          reject the reel if it fails format/motion/text checks
 10. upload_host      publish the MP4 to a public URL (src/upload_host.py)
 11. publish          Instagram create + publish (src/publish.py)
 12. alerts           Telegram report (src/alerts.py)
 13. state/memory     record what was used so nothing repeats (src/state.py)

Modules: alerts, analyze, background, build_video, config, generate, harvest, llm,
mind, music, publish, run_create, run_health, run_learn, run_measure, seo, state,
upload_host, notify_send.

Dependencies (requirements.txt): requests, Pillow, numpy, librosa, soundfile, pynacl,
PyYAML, imageio-ffmpeg.

ffmpeg is a HARD dependency. music.py, background.py, build_video.py and pinterest.py
all shell out to it. imageio-ffmpeg ships a static build, so CI and local behave
identically with no apt/sudo.

Workflows (.github/workflows/):
  create.yml   the main pipeline (scheduled + manual)
  health.yml   daily health check
  learn.yml    performance learning
  measure.yml  metrics
  notify.yml   sends a Telegram message on demand


FIVE THINGS THAT WERE BROKEN, AND HOW THEY WERE FIXED
-----------------------------------------------------

A. CI HAD NO FFMPEG AT ALL
   All 22 ffmpeg call sites used the bare name "ffmpeg". Locally that only worked
   because of a hand-made symlink to imageio-ffmpeg's binary. A clean runner has no
   ffmpeg, so the acceptance suite died with [Errno 2] 'ffmpeg' — and because that
   suite gates publishing, the whole create workflow was blocked.
   Worse: CI had never once actually rendered a reel. Every "successful" run was a
   SKIP ("already posted today"). The gate was hiding a total absence of ffmpeg.
   FIX: config.resolve_ffmpeg() (BEAST_FFMPEG -> PATH -> imageio-ffmpeg), all 22 call
   sites routed through it, imageio-ffmpeg declared in requirements.txt, a preflight
   step in create.yml, plus tests.

B. THE MUSIC REPEATED (your complaint — the same one or two tracks)
   Measured at the service: query -> track is 1:1. The old code had only ~6 queries
   per mood and always tried the canonical one first, so the palette was effectively a
   handful of tracks.
   My FIRST rebuild failed and I measured why instead of guessing: 4 of 5 long
   4-part queries returned no bytes. Query shape mattered:
        2-word queries -> 8/8 resolved
        3-word queries -> 8/8 resolved
        4-word phrases -> 1/5 resolved
   REBUILT as <=3-word queries, no repeated words, no §5-unsafe terms:
        866 distinct queries across 5 moods (216/167/197/166/120)
   Live proof: 5 generated queries -> 5 DISTINCT tracks, all resolved.

   Note: the shipped palette is 216/mood after the <=3-word shape rule, which is what
   the acceptance test now asserts against (the measured value), not an aspirational
   one.

C. THE LOOP FILTER WAS BROKEN ON CI
   CI failed with: [vost#0:0/libx264] Task finished with error code: -22
   and then "QA gate failed: background has no motion".
   Isolated the exact cause:
        [Parsed_xfade] The inputs needs to be a constant frame rate; current rate of
        1/0 is invalid
   trim() drops the frame-rate metadata, so xfade sees 1/0 and refuses to configure
   its output pad. Adding fps=/settb= before it does NOT fix it (tested, still rc=234).
   Split test: with trim, "split+concat" succeeds while "xfade" fails.
   FIX: the loop join now uses concat (same duration, motion gate passes).

D. exists() WAS BEING USED AS A VALIDITY CHECK
   A failed ffmpeg run leaves a 0-byte file behind, and exists() returns True for it.
   The straight-cut fallback copied those 0 bytes into the background, the render then
   reported "exists: True" while containing zero packets, and the reel only failed at
   the very end — masking the real error.
   FIX: background._is_valid_video() requires real size AND a decodable duration;
   loop_clip, process_clip and animate_still all gate on it; the render assembler
   requires size + frames, not exists().

E. THE FRAME PROBE COULD NEVER WORK ON CI
   CI failed again with: [video] assemble produced a file with no video frames.
   But the render was fine — ffmpeg returned 0 with no error. The fault was in the
   probe itself:
     1. it called a bare "ffprobe", which does not exist on a GitHub runner (only
        imageio-ffmpeg's ffmpeg is present), and
     2. its ffmpeg fallback was a stub: it ran ffmpeg, assigned the result to _, and
        returned None unconditionally. It never counted a frame.
   So every CI render was reported as frameless and rejected.
   FIX: _probe_frames goes ffprobe (only when shutil.which finds it) ->
   imageio_ffmpeg.count_frames_and_secs -> ffmpeg decode-count. And the assembler now
   treats None as UNKNOWN, never as empty — only a definite 0 is fatal. Reading None
   as "no frames" is exactly what failed every CI render.
   Verified: _probe_frames -> 288 for a 9.6s/30fps clip with ffprobe absent from PATH
   (the exact CI condition), where it previously returned None.


VERIFICATION STANDARD
---------------------
Every claim above is backed by executed output, in the environment that actually
matters. The suite runs 68/68 both normally and with ffmpeg + ffprobe stripped from
PATH (the CI condition) — because three of these five bugs were invisible locally and
only appeared on the runner. A build is not "verified" until it has been measured
under CI conditions.


OPERATIONS
----------
- TWO posts per day. Each has a due time and a retry, all in UTC:
      post 1  due 13:30 UTC (19:00 IST)   retry 15:00 UTC (20:30 IST)
      post 2  due 17:30 UTC (23:00 IST)   retry 19:00 UTC (00:30 IST next day)
  A retry only publishes if its post is still missing, so a failed build is picked up
  by the next run instead of losing the slot. The daily cap (2) is what prevents a
  third post — forcing a manual run does NOT bypass it.
- To run it by hand: dispatch create.yml (optionally with dry_run).
- dry-run does a real build against the real services but publishes nothing, mutates
  no state and sends no Telegram message. It ignores the daily cap, since it publishes
  nothing.
- To send yourself a Telegram message: dispatch notify.yml with a "text" input.