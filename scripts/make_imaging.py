#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — unified, preset-driven imaging generator (Google-only).

EVERY show is an imaging show. The roster is derived straight from the presets
(shows/<slug>.json) plus the shared "station" set — there is no separate,
hardcoded imaging list to drift out of sync with Studio. Each show gets the
full, uniform set of assets under its OWN folder:

    assets/<show>/intro/        spoken station ID (Gemini TTS over a bed)
    assets/<show>/outro/        spoken sign-off  (Gemini TTS over a bed)
    assets/<show>/bed/          music bed        (Lyria 3)
    assets/<show>/transition/   short whoosh/transition sting (Lyria 3)
    assets/<show>/sting/        several bumper stings (Lyria 3)

  * music beds + transition + stings -> Lyria 3   (scripts/lyria_music.py)
  * spoken intros / outros           -> Gemini TTS (scripts/tts_engine.py)
                                        mixed under a fresh bed (audio_mix.py)

Prompts/scripts: the well-known shows ship CURATED prompts; every other preset
gets sensible defaults synthesized from its name (override per-preset by adding
an optional "imaging" block to its shows/<slug>.json).

VARIANT MODEL: every run writes a NEW, timestamped file next to the existing
ones — it never overwrites. The app automatically uses the MOST RECENT variant
for each (show, kind) unless the user pins one as the default (assets_lib /
the Imaging tab). So "regenerate" just means "make a newer one".

  python3 scripts/make_imaging.py                       # everything, all shows
  python3 scripts/make_imaging.py --list                # show the plan, no calls
  python3 scripts/make_imaging.py --show station        # one show (repeatable)
  python3 scripts/make_imaging.py --show cook --show sport
  python3 scripts/make_imaging.py --kind sting          # only stings (any show)
  python3 scripts/make_imaging.py --kind sting --replace # regen stings clean
  python3 scripts/make_imaging.py --show sport --kind bed
  python3 scripts/make_imaging.py --show station --kind intro --make-default

Flags:
  --show / --set   limit to a show (repeatable); default = all shows
  --kind           limit to a kind: bed|intro|outro|transition|sting (repeatable)
  --list           print the plan and exit (changes nothing)
  --dry-run        print the steps but skip the Gemini calls
  --replace        back up & remove existing variants in scope first
  --make-default   pin each freshly generated variant as the default

  NOTE: Lyria 3 is NOT on the Gemini free tier. This needs a PAID
  GEMINI_API_KEY and outbound access to generativelanguage.googleapis.com.
  Run it on your own machine, not in a sandbox.

The `bouzid imaging` command is a thin wrapper around this script.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import assets_lib   # noqa: E402  (variant resolver + pinned defaults + roster)
import tts_engine   # noqa: E402  (Google Gemini TTS wrapper)
import audio_mix    # noqa: E402  (ffmpeg mixer: build / make_body / concat)

try:
    import lyria_music   # noqa: E402  (Google Lyria 3 music)
except Exception:        # noqa: BLE001
    lyria_music = None

KINDS = ["bed", "intro", "outro", "transition", "sting"]
KIND_ORDER = {"bed": 0, "intro": 1, "outro": 2, "transition": 3, "sting": 4}
STING_LUFS = audio_mix.DEFAULTS.get("lufs", -12.0)

WHOOSH = (
    "short, clean, modern radio station whoosh transition sting, "
    "punchy, no vocals, instrumental"
)

DEFAULT_STINGS = [
    "bright radio bumper sting, rising synth and a clean hit, energetic, "
    "instrumental",
    "warm oud flourish sting with a soft percussion hit, Tunisian flavor, "
    "instrumental",
    "modern whoosh into a deep boom transition sting, clean, instrumental",
]

# --------------------------------------------------------------------------- #
#  Curated prompts/scripts for every show. Anything not listed here
#  (or overridden by a preset "imaging" block) is synthesized from the name.
#  Keys: voice, bed, intro, outro, transition, transition_secs,
#        stings (list), sting_count, sting_secs.
#
#  Each bed prompt is production-grade: key, BPM, instrumentation, arrangement,
#  mood — Lyria 3 receives "Instrumental only, no vocals, no lyrics..." prefix
#  automatically (see lyria_music.generate), so "instrumental" / "no vocals"
#  are included only for clarity at the model level.
#  Each sting/transition prompt is designed for a ~2.5–3 s short edit.
# --------------------------------------------------------------------------- #
STATION = {
    "name": "بوزيد إف إم",
    "voice": "Kore",
    "bed": (
        "Warm, confident Tunisian radio station music bed in D minor, 90 BPM. "
        "Rich oud melody with light hand percussion (darbouka, riq) over warm "
        "pad strings and a steady kick-hat groove. Elegant and uplifting, "
        "broadcast-ready. Arrangement: gentle intro (4 bars) -> main theme "
        "(8 bars) -> variation (8 bars) -> return to theme (8 bars) -> soft "
        "outro (4 bars). No vocals."
    ),
    "intro": "بوزيد إف إم، صوت سيدي بوزيد والجهة. أخبار، وثقافة، وموسيقى، على مدار اليوم.",
    "outro": "كان معكم بوزيد إف إم. شكراً على المتابعة، وإلى اللقاء.",
    "transition": WHOOSH, "transition_secs": 2.8,
    "stings": [
        "bright radio bumper sting, rising synth and a clean hit, energetic, "
        "instrumental",
        "warm oud flourish sting with a soft percussion hit, Tunisian flavor, "
        "instrumental",
        "modern whoosh into a deep boom transition sting, clean, instrumental",
        "playful marimba and clap bumper sting, light and friendly, "
        "instrumental",
        "cinematic riser into a confident brass stab sting, instrumental",
    ],
    "sting_secs": 3.0,
}

CURATED = {
    # -- 1. COOK --
    "cook": {
        "voice": "Aoede",
        "bed": (
            "Light, warm cooking-show music bed in G major, 98 BPM. "
            "Nylon-string acoustic guitar fingerpicking a gentle arpeggio pattern, "
            "soft brushed snare and hi-hat, warm upright piano comping, light "
            "shaker percussion. Cheerful and homely. Arrangement: intro (4 bars) "
            "-> verse (8 bars) -> chorus (8 bars) -> verse (8 bars) -> "
            "chorus (8 bars) -> outro (4 bars). No vocals."
        ),
        "intro": "كوك مع إيّة، على بوزيد إف إم. اليوم نطيّبو وصفة تونسية بنينة وساهلة.",
        "outro": "بالصحة والهنا. كان معاكم برنامج كوك مع إيّة على بوزيد إف إم.",
        "transition": (
            "short cheerful kitchen transition sting, bright acoustic guitar "
            "chime and a soft percussive hit, warm and inviting, instrumental"
        ),
        "transition_secs": 2.6,
        "stings": [
            "bright acoustic guitar strum with a light percussive hit, warm "
            "kitchen vibe, instrumental",
            "cheerful marimba and shaker sting, playful and homely, "
            "instrumental",
            "gentle rising chime into a soft clap, warm and friendly, "
            "instrumental",
        ],
        "sting_secs": 2.8,
    },
    # -- 2. SIDI BOUZID --
    "sidibouzid": {
        "voice": "Charon",
        "bed": (
            "Cinematic documentary music bed in C minor, 76 BPM. "
            "Warm sustained string ensemble with a subtle oud counter-melody, "
            "soft piano chords, and a slow, deliberate kick-snare pulse. "
            "Reflective and dignified, building in texture across the full "
            "duration. Arrangement: sparse intro (4 bars) -> theme (8 bars) -> "
            "strings build (8 bars) -> full ensemble (8 bars) -> fade (4 bars)."
            " No vocals."
        ),
        "intro": "حكاية سيدي بوزيد، على بوزيد إف إم. قصة من تاريخ المدينة والجهة.",
        "outro": "إلى هنا تنتهي حكاية اليوم، من بوزيد إف إم.",
        "transition": (
            "epic cinematic documentary impact transition sting, deep "
            "cellos and a brass swell, dignified, instrumental"
        ),
        "transition_secs": 2.6,
        "stings": [
            "cinematic brass and string stab, deep and resonant, "
            "documentary style, instrumental",
            "slow dramatic riser into a soft timpani hit, dignified, "
            "instrumental",
            "warm string swell with a delicate piano note, reflective, "
            "instrumental",
        ],
        "sting_secs": 3.0,
    },
    # -- 3. FOLKTALE --
    "folktale": {
        "voice": "Sulafat",
        "bed": (
            "Mystical traditional storytelling music bed in A minor, 82 BPM. "
            "Soft ney flute melody with gentle frame-drum and riq percussion, "
            "warm low-string drones, and occasional soft chimes. Dreamy and "
            "warm, immersive. Arrangement: flute solo (8 bars) -> percussion "
            "enters (8 bars) -> full texture (8 bars) -> return to flute "
            "solo (6 bars) -> fade. No vocals."
        ),
        "intro": "حكاية وحكاية، على بوزيد إف إم.",
        "outro": "وتوفّى الحكاية. كان معاكم بوزيد إف إم.",
        "transition": (
            "gentle magical chime transition sting, soft flute note "
            "and a delicate triangle ring, dreamy, instrumental"
        ),
        "transition_secs": 2.6,
        "stings": [
            "soft magical chime with a flute tail, mystical and warm, "
            "instrumental",
            "gentle frame-drum roll into a triangle hit, dreamy, "
            "instrumental",
            "delicate harp glissando with a soft wind chime, storytelling "
            "vibe, instrumental",
        ],
        "sting_secs": 2.8,
    },
    # -- 4. HERITAGE MUSIC --
    "heritagemusic": {
        "voice": "Gacrux",
        "bed": (
            "Traditional Tunisian heritage music bed in D minor, 88 BPM. "
            "Malouf-inspired string ensemble (violin, viola, cello) with "
            "frame-drum (mizhar) and darbouka percussion, warm kanun "
            "ornamentation. Elegant and timeless. Arrangement: kanun intro "
            "(4 bars) -> string theme (8 bars) -> percussion joins (8 bars) -> "
            "full ensemble (8 bars) -> resolution (4 bars). No vocals."
        ),
        "intro": "موسيقى من التراث، على بوزيد إف إم.",
        "outro": "كان معاكم ركن التراث على بوزيد إف إم.",
        "transition": (
            "elegant malouf string flourish transition sting, "
            "frame-drum hit and a kanun ornament, instrumental"
        ),
        "transition_secs": 2.6,
        "stings": [
            "traditional malouf string flourish with frame-drum hit, "
            "elegant, instrumental",
            "kanun glissando with soft darbouka tap, heritage style, "
            "instrumental",
            "violin swell into a mizhar percussion hit, dignified, "
            "instrumental",
        ],
        "sting_secs": 2.8,
    },
    # -- 5. SPORT --
    "sport": {
        "voice": "Puck",
        "bed": (
            "Energetic sports radio music bed in E-flat minor, 110 BPM. "
            "Boom-bap kick and snare, punchy brass stabs, driving synth bass, "
            "and hype vocal chops (wordless). High energy throughout. "
            "Arrangement: intro with riser (4 bars) -> beat drops (8 bars) -> "
            "brass section (8 bars) -> breakdown (4 bars) -> final drop "
            "(8 bars) -> cut. No lyrical vocals."
        ),
        "intro": "رياضة بوزيد، على بوزيد إف إم. آخر أخبار الرياضة.",
        "outro": "كان معاكم ركن الرياضة على بوزيد إف إم.",
        "transition": (
            "punchy sports hit transition sting, brass stab, drum hit, "
            "and a sub-boom, energetic, instrumental"
        ),
        "transition_secs": 2.4,
        "stings": [
            "punchy brass stab with a kick drum hit, energetic and bold, "
            "instrumental",
            "short hype riser into a snare hit, sports bumper style, "
            "instrumental",
            "aggressive synth buzz into a crash cymbal, driving, "
            "instrumental",
        ],
        "sting_secs": 2.6,
    },
    # -- 6. NEWS --
    "news": {
        "voice": "Kore",
        "bed": (
            "Authoritative news radio music bed in C major, 85 BPM. "
            "Clear, confident piano melody with soft sustained strings, "
            "a steady kick-snare pulse, and subtle tympani accents. Clean "
            "and professional. Arrangement: piano intro (4 bars) -> strings "
            "enter (8 bars) -> full rhythm section (8 bars) -> return to "
            "piano (8 bars) -> resolve (4 bars). No vocals."
        ),
        "intro": "نشرة الأخبار، على بوزيد إف إم. أحداث الساعة من سيدي بوزيد والجهة.",
        "outro": "كانت معكم نشرة الأخبار على بوزيد إف إم. إلى اللقاء.",
        "transition": (
            "clean news transition sting, soft tympani and a swift "
            "string glissando, authoritative, instrumental"
        ),
        "transition_secs": 2.4,
        "stings": [
            "confident piano and string stab, news bulletin style, "
            "instrumental",
            "short tympani hit with a brass swell, authoritative, "
            "instrumental",
            "clean glockenspiel chime with soft percussion, "
            "professional, instrumental",
        ],
        "sting_secs": 2.6,
    },
    # -- 7. RAMADAN --
    "ramadan": {
        "voice": "Sulafat",
        "bed": (
            "Spiritual, warm Ramadan music bed in D minor, 80 BPM. "
            "Soulful ney flute melody over soft pad strings, gentle "
            "darbouka and frame-drum percussion, warm bass drone. "
            "Peaceful and contemplative. Arrangement: ney intro (8 bars) -> "
            "percussion enters (8 bars) -> full texture (8 bars) -> ney "
            "variation (8 bars) -> fade (4 bars). No vocals."
        ),
        "intro": "رفيق رمضان، على بوزيد إف إم. حلقات دينية وإيمانية في شهر الخير.",
        "outro": "كان معكم برنامج رفيق رمضان على بوزيد إف إم. تقبل الله منا ومنكم.",
        "transition": (
            "gentle Ramadan transition sting, soft ney note with "
            "a warm darbouka tap, spiritual, instrumental"
        ),
        "transition_secs": 2.8,
        "stings": [
            "warm ney flute note with soft frame-drum hit, spiritual "
            "atmosphere, instrumental",
            "gentle string swell with a darbouka accent, warm and "
            "reverent, instrumental",
            "soft chime with pad tail, peaceful Ramadan vibe, "
            "instrumental",
        ],
        "sting_secs": 3.0,
    },
    # -- 8. FARMER --
    "farmer": {
        "voice": "Achird",
        "bed": (
            "Pastoral, earthy farming music bed in G major, 92 BPM. "
            "Acoustic guitar fingerpicking, mandolin counter-melody, "
            "light shaker and brushed snare, warm harmonica pads. "
            "Grounding and optimistic. Arrangement: guitar solo (8 bars) -> "
            "mandolin enters (8 bars) -> rhythm section (8 bars) -> "
            "return to guitar (8 bars) -> fade (4 bars). No vocals."
        ),
        "intro": "نشرة الفلاحة، على بوزيد إف إم. آخر المستجدات الفلاحية ونصائح الموسم.",
        "outro": "كانت معكم نشرة الفلاحة على بوزيد إف إم. والسلام.",
        "transition": (
            "short rustic transition sting, acoustic guitar strum "
            "with a soft mandolin note, pastoral, instrumental"
        ),
        "transition_secs": 2.4,
        "stings": [
            "acoustic guitar and mandolin flourish, rustic and warm, "
            "instrumental",
            "light harmonica note with a shaker hit, countryside feel, "
            "instrumental",
            "gentle wood block tap with guitar chime, natural, "
            "instrumental",
        ],
        "sting_secs": 2.6,
    },
    # -- 9. HEALTH --
    "health": {
        "voice": "Sulafat",
        "bed": (
            "Calm, caring health advice music bed in C major, 86 BPM. "
            "Warm piano arpeggios with soft string pad, gentle kick-snare "
            "pulse, delicate chimes. Soothing and reassuring. Arrangement: "
            "piano solo (8 bars) -> strings enter (8 bars) -> light "
            "percussion (8 bars) -> return to piano (8 bars) -> fade (4 bars)."
            " No vocals."
        ),
        "intro": "نصيحة صحية، على بوزيد إف إم. صحتك تهمنا.",
        "outro": "كان معكم ركن نصيحة صحية على بوزيد إف إم. في أمان الله.",
        "transition": (
            "soft, gentle health transition sting, piano note with "
            "a warm string swell, caring, instrumental"
        ),
        "transition_secs": 2.6,
        "stings": [
            "warm piano chord with soft chime, reassuring and calm, "
            "instrumental",
            "gentle string swell with a delicate bell tone, peaceful, "
            "instrumental",
            "soft harp glissando with pad tail, soothing, instrumental",
        ],
        "sting_secs": 2.8,
    },
    # -- 10. FAMILY --
    "family": {
        "voice": "Aoede",
        "bed": (
            "Heartwarming family radio bed in F major, 94 BPM. "
            "Warm acoustic guitar strumming, soft piano, light shaker "
            "and cajon percussion, cheerful whistled melody. Welcoming "
            "and cosy. Arrangement: guitar intro (4 bars) -> whistled "
            "theme (8 bars) -> full band (8 bars) -> piano variation "
            "(8 bars) -> guitar outro (4 bars). No vocals."
        ),
        "intro": "ركن الأسرة، على بوزيد إف إم. نصائح وارشادات للأسرة التونسية.",
        "outro": "كان معكم ركن الأسرة على بوزيد إف إم. إلى اللقاء.",
        "transition": (
            "warm family transition sting, bright acoustic guitar "
            "strum and a soft clap, heartwarming, instrumental"
        ),
        "transition_secs": 2.4,
        "stings": [
            "acoustic guitar strum with a cheerful clap, warm and "
            "inviting, instrumental",
            "soft piano note with a light shaker, family-friendly, "
            "instrumental",
            "bright whistled phrase with gentle percussion, cosy, "
            "instrumental",
        ],
        "sting_secs": 2.6,
    },
    # -- 11. HOROSCOPE --
    "horoscope": {
        "voice": "Despina",
        "bed": (
            "Mysterious, ethereal horoscope music bed in B-flat minor, 78 BPM. "
            "Dreamy synth pads, glockenspiel melody, soft electronic "
            "percussion, occasional sparkle effects. Celestial and intriguing. "
            "Arrangement: pad intro (4 bars) -> glockenspiel theme (8 bars) -> "
            "beat enters (8 bars) -> filtered breakdown (8 bars) -> "
            "resolution (4 bars). No vocals."
        ),
        "intro": "برج اليوم، على بوزيد إف إم. توقعات الأبراج ليومك.",
        "outro": "كان معكم برج اليوم على بوزيد إف إم. ونراكم غداً.",
        "transition": (
            "mystical horoscope transition sting, celeste chime with "
            "a soft synth riser, ethereal, instrumental"
        ),
        "transition_secs": 2.6,
        "stings": [
            "glockenspiel and synth chime, mysterious and ethereal, "
            "instrumental",
            "soft sparkle effect with a pad swell, celestial, "
            "instrumental",
            "gentle bell tone with a filtered riser, horoscope vibe, "
            "instrumental",
        ],
        "sting_secs": 2.8,
    },
    # -- 12. QUIZ --
    "quiz": {
        "voice": "Puck",
        "bed": (
            "Playful, bright quiz show music bed in D major, 104 BPM. "
            "Pizzicato strings, bright xylophone, light funk guitar chops, "
            "tight kick-snare with handclaps. Energetic and fun. "
            "Arrangement: pizzicato intro (4 bars) -> theme (8 bars) -> "
            "funk section (8 bars) -> pause-drop (4 bars) -> theme out "
            "(8 bars). No vocals."
        ),
        "intro": "سؤال اليوم، على بوزيد إف إم. شاركونا الإجابة.",
        "outro": "كان معكم سؤال اليوم على بوزيد إف إم. إلى المزيد من الأسئلة.",
        "transition": (
            "bright quiz transition sting, pizzicato string flourish "
            "with a handclap, playful, instrumental"
        ),
        "transition_secs": 2.4,
        "stings": [
            "xylophone and pizzicato string hit, bright and playful, "
            "instrumental",
            "short funk guitar chop with a handclap, energetic quiz "
            "vibe, instrumental",
            "rising synth with a clap and a bell, game show style, "
            "instrumental",
        ],
        "sting_secs": 2.4,
    },
    # -- 13. DID YOU KNOW? --
    "didyouknow": {
        "voice": "Sadaltager",
        "bed": (
            "Curious, light educational music bed in G major, 88 BPM. "
            "Bouncy marimba melody, soft pizzicato strings, light kick "
            "and hi-hat, occasional triangle accents. Inquisitive and "
            "warm. Arrangement: marimba intro (4 bars) -> strings enter "
            "(8 bars) -> percussion (8 bars) -> variation (8 bars) -> "
            "marimba outro (4 bars). No vocals."
        ),
        "intro": "هل تعلم؟ على بوزيد إف إم. معلومة جديدة كل يوم.",
        "outro": "كان معكم ركن هل تعلم على بوزيد إف إم. إلى اللقاء.",
        "transition": (
            "curious did-you-know transition sting, marimba note with "
            "a triangle ring, light and inquisitive, instrumental"
        ),
        "transition_secs": 2.4,
        "stings": [
            "bright marimba run with a soft triangle hit, curious "
            "and light, instrumental",
            "pizzicato string pluck with a shaker, educational vibe, "
            "instrumental",
            "short wood block and bell chime, playful and inquisitive, "
            "instrumental",
        ],
        "sting_secs": 2.6,
    },
    # -- 14. SONG STORY --
    "songstory": {
        "voice": "Gacrux",
        "bed": (
            "Narrative, warm music documentary bed in A minor, 84 BPM. "
            "Solo piano intro with warm string counterpoint, soft kick "
            "and brushed snare, warm bass. Storytelling and emotional. "
            "Arrangement: piano solo (8 bars) -> strings enter (8 bars) -> "
            "full band (8 bars) -> piano variation (8 bars) -> fade (4 bars)."
            " No vocals."
        ),
        "intro": "الأغنية وقصتها، على بوزيد إف إم. حكاية كل أغنية.",
        "outro": "كان معكم برنامج الأغنية وقصتها على بوزيد إف إم. مع السلامة.",
        "transition": (
            "warm narrative transition sting, soft piano note with "
            "a string swell, musical storytelling, instrumental"
        ),
        "transition_secs": 2.8,
        "stings": [
            "gentle piano chord with a string tail, warm and narrative, "
            "instrumental",
            "soft brushed drum roll into a piano note, musical story "
            "vibe, instrumental",
            "warm bass note with a delicate chime, reflective, "
            "instrumental",
        ],
        "sting_secs": 2.8,
    },
    # -- 15. WORD --
    "word": {
        "voice": "Iapetus",
        "bed": (
            "Scholarly, thoughtful word-origin bed in C minor, 80 BPM. "
            "Warm oud and ney flute dialogue, soft frame-drum, low "
            "string drone. Contemplative and learned. Arrangement: oud "
            "solo (8 bars) -> ney enters (8 bars) -> percussion (8 bars) -> "
            "oud and ney duet (8 bars) -> fade (4 bars). No vocals."
        ),
        "intro": "كلمة وأصلها، على بوزيد إف إم. رحلة في اللغة العربية.",
        "outro": "كان معكم ركن كلمة وأصلها على بوزيد إف إم. إلى اللقاء.",
        "transition": (
            "thoughtful word transition sting, soft oud pluck with "
            "a ney breath, scholarly, instrumental"
        ),
        "transition_secs": 2.6,
        "stings": [
            "warm oud strum with soft frame-drum tap, thoughtful "
            "atmosphere, instrumental",
            "ney flute note with a low string drone, reflective, "
            "instrumental",
            "soft darbouka tap with a kanun ornament, learned vibe, "
            "instrumental",
        ],
        "sting_secs": 2.8,
    },
    # -- 16. FIGURES --
    "figures": {
        "voice": "Alnilam",
        "bed": (
            "Historical documentary music bed in D minor, 76 BPM. "
            "Cinematic string orchestra, warm brass chorale, timpani "
            "accents, soft piano. Dignified and inspiring. Arrangement: "
            "string intro (4 bars) -> brass enters (8 bars) -> full "
            "orchestra (8 bars) -> chamber section (8 bars) -> finale "
            "(4 bars). No vocals."
        ),
        "intro": "أعلام من الجهة، على بوزيد إف إم. شخصيات صنعت تاريخ سيدي بوزيد.",
        "outro": "كان معكم ركن أعلام من الجهة على بوزيد إف إم. والسلام.",
        "transition": (
            "epic historical transition sting, brass swell with a "
            "timpani hit, dignified, instrumental"
        ),
        "transition_secs": 2.8,
        "stings": [
            "cinematic brass and timpani stab, historical documentary "
            "style, instrumental",
            "warm string and french horn swell, inspiring, "
            "instrumental",
            "soft piano chord with orchestral hit, dignified, "
            "instrumental",
        ],
        "sting_secs": 3.0,
    },
    # -- 17. HOME --
    "home": {
        "voice": "Callirrhoe",
        "bed": (
            "Practical, cosy home advice bed in C major, 92 BPM. "
            "Bright acoustic guitar, soft ukulele strum, light kick "
            "and tambourine, warm bass. Friendly and helpful. "
            "Arrangement: guitar intro (4 bars) -> ukulele theme (8 bars) -> "
            "full band (8 bars) -> guitar variation (8 bars) -> strum "
            "outro (4 bars). No vocals."
        ),
        "intro": "نصيحة البيت، على بوزيد إف إم. نصائح عملية للمنزل.",
        "outro": "كان معكم ركن نصيحة البيت على بوزيد إف إم. في أمان الله.",
        "transition": (
            "cosy home transition sting, ukulele strum with a soft "
            "tambourine shake, friendly, instrumental"
        ),
        "transition_secs": 2.4,
        "stings": [
            "bright ukulele strum with a tambourine hit, cosy and "
            "friendly, instrumental",
            "soft acoustic guitar note with a wood block tap, "
            "practical vibe, instrumental",
            "warm bass note with a light shaker, homely, instrumental",
        ],
        "sting_secs": 2.6,
    },
    # -- 18. PRAYER --
    "prayer": {
        "voice": "Vindemiatrix",
        "bed": (
            "Reverent, peaceful prayer music bed in D minor, 70 BPM. "
            "Solo ney flute over soft synth pads and a warm bass drone, "
            "distant frame-drum accents. Spiritual and calm. "
            "Arrangement: ney alone (8 bars) -> pads enter (8 bars) -> "
            "percussion lightly enters (8 bars) -> return to ney (8 bars) -> "
            "silence (4 bars). No vocals."
        ),
        "intro": "مواقيت الصلاة، على بوزيد إف إم. تذكير بأوقات الصلاة.",
        "outro": "كان معكم مواقيت الصلاة على بوزيد إف إم. في رعاية الله.",
        "transition": (
            "peaceful prayer transition sting, soft ney note with a "
            "reverent pad swell, spiritual, instrumental"
        ),
        "transition_secs": 3.0,
        "stings": [
            "soft ney flute note with gentle pad tail, reverent, "
            "instrumental",
            "warm bass drone with a distant chime, peaceful, "
            "instrumental",
            "gentle frame-drum tap with pad atmosphere, spiritual, "
            "instrumental",
        ],
        "sting_secs": 3.0,
    },
    # -- 19. EVENTS --
    "events": {
        "voice": "Laomedeia",
        "bed": (
            "Bright, informative events calendar bed in C major, 96 BPM. "
            "Steady acoustic guitar strum, light piano chords, crisp kick "
            "and hi-hat, cheerful brass hits. Upbeat and clear. "
            "Arrangement: guitar intro (4 bars) -> piano theme (8 bars) -> "
            "brass accents (8 bars) -> breakdown (8 bars) -> theme out "
            "(4 bars). No vocals."
        ),
        "intro": "روزنامة الجهة، على بوزيد إف إم. أحداث وفعاليات سيدي بوزيد.",
        "outro": "كانت معكم روزنامة الجهة على بوزيد إف إم. إلى اللقاء.",
        "transition": (
            "bright events transition sting, piano and brass hit "
            "with a crisp snare, informative, instrumental"
        ),
        "transition_secs": 2.4,
        "stings": [
            "bright brass and piano stab, informative and clear, "
            "instrumental",
            "acoustic guitar strum with a cheerful brass note, "
            "upbeat events vibe, instrumental",
            "crisp snare hit with a glockenspiel chime, lively, "
            "instrumental",
        ],
        "sting_secs": 2.6,
    },
    # -- 20. PROVERB --
    "proverb": {
        "voice": "Rasalgethi",
        "bed": (
            "Wise, thoughtful proverb music bed in E minor, 78 BPM. "
            "Soulful oud solo with soft string pad, gentle darbouka "
            "accents, warm bass. Reflective and grounded. Arrangement: "
            "oud intro (8 bars) -> strings enter (8 bars) -> percussion "
            "lightly (8 bars) -> oud variation (8 bars) -> fade (4 bars)."
            " No vocals."
        ),
        "intro": "مثل اليوم، على بوزيد إف إم. حكمة من التراث التونسي.",
        "outro": "كان معكم ركن مثل اليوم على بوزيد إف إم. مع السلامة.",
        "transition": (
            "wise proverb transition sting, warm oud note with "
            "a gentle string swell, thoughtful, instrumental"
        ),
        "transition_secs": 2.8,
        "stings": [
            "warm oud strum with a soft string pad, wise and "
            "thoughtful, instrumental",
            "gentle darbouka tap with a bass note, grounded, "
            "instrumental",
            "soft pad swell with a delicate chime, reflective "
            "proverb vibe, instrumental",
        ],
        "sting_secs": 2.8,
    },
    # -- 21. BUDGET --
    "budget": {
        "voice": "Algieba",
        "bed": (
            "Friendly, practical budget cooking bed in C major, 94 BPM. "
            "Acoustic guitar strumming, soft accordion melody, light "
            "tambourine and kick, warm upright bass. Economical and "
            "cheerful. Arrangement: guitar intro (4 bars) -> accordion "
            "theme (8 bars) -> full band (8 bars) -> guitar variation "
            "(8 bars) -> strum outro (4 bars). No vocals."
        ),
        "intro": "وصفة اقتصادية، على بوزيد إف إم. طبخ بنين وبأسعار معقولة.",
        "outro": "كان معكم ركن وصفة اقتصادية على بوزيد إف إم. بالصحة والهنا.",
        "transition": (
            "friendly budget transition sting, accordion note with "
            "a soft guitar strum, warm and practical, instrumental"
        ),
        "transition_secs": 2.4,
        "stings": [
            "bright accordion chord with acoustic guitar strum, "
            "friendly and warm, instrumental",
            "soft tambourine shake with a bass note, practical "
            "kitchen vibe, instrumental",
            "gentle wood block with a guitar chime, economical "
            "and cheerful, instrumental",
        ],
        "sting_secs": 2.6,
    },
}


# --------------------------------------------------------------------------- #
#  Build the imaging plan from the presets (+ the shared station set)
# --------------------------------------------------------------------------- #
def _load_preset(slug):
    p = os.path.join(ROOT, "shows", slug + ".json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def _default_bed_prompt(name):
    return ("warm, modern Tunisian radio music bed for the show \u00ab%s\u00bb, "
            "tasteful instrumentation, uplifting, no vocals, instrumental "
            "broadcast imaging" % name)


def _default_intro(name):
    return "%s\u060c \u0639\u0644\u0649 \u0625\u0630\u0627\u0639\u0629 " \
           "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645." % name


def _default_outro(name):
    return "\u0643\u0627\u0646 \u0645\u0639\u0627\u0643\u0645 %s \u0639\u0644\u0649 " \
           "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645." % name


def _set_path(slug, kind):
    """Canonical stem template: assets/<slug>/<kind>/<slug>_<kind>.mp3"""
    return "assets/%s/%s/%s_%s.mp3" % (slug, kind, slug, kind)


def build_set(slug):
    """Assemble the full asset list for one show (station or a preset)."""
    if slug == "station":
        c = dict(STATION)
        name = c.get("name", "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645")
        voice = c.get("voice", "Kore")
    else:
        preset = _load_preset(slug)
        name = preset.get("name") or slug
        c = dict(CURATED.get(slug, {}))
        c.update({k: v for k, v in (preset.get("imaging") or {}).items()
                  if v is not None})
        voice = c.get("voice") or preset.get("voice") or "Kore"

    bed_prompt = c.get("bed") or _default_bed_prompt(name)
    intro_script = c.get("intro") or _default_intro(name)
    outro_script = c.get("outro") or _default_outro(name)
    trans_prompt = c.get("transition") or WHOOSH
    trans_secs = float(c.get("transition_secs", 2.6))
    stings = c.get("stings") or DEFAULT_STINGS
    sting_count = int(c.get("sting_count", len(stings)))
    sting_secs = float(c.get("sting_secs", 3.0))

    assets = [
        {"kind": "bed", "path": _set_path(slug, "bed"), "music": bed_prompt},
        {"kind": "intro", "path": _set_path(slug, "intro"),
         "script": intro_script},
        {"kind": "outro", "path": _set_path(slug, "outro"),
         "script": outro_script},
        {"kind": "transition", "path": _set_path(slug, "transition"),
         "music": trans_prompt, "secs": trans_secs},
    ]
    for i in range(max(sting_count, 0)):
        assets.append({"kind": "sting", "path": _set_path(slug, "sting"),
                       "music": stings[i % len(stings)], "secs": sting_secs})
    return {"voice": voice, "assets": assets}


def _all_slugs():
    return ["station"] + assets_lib.preset_slugs()


def build_sets():
    return {slug: build_set(slug) for slug in _all_slugs()}


# Computed once at import: station + every preset = the full imaging roster.
SETS = build_sets()


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _abs(rel):
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)


def _rel(p):
    try:
        return os.path.relpath(p, ROOT)
    except ValueError:
        return p


def _variant_path(asset, stamp, idx=0):
    """Build a fresh, timestamped variant path from an asset's stem template.
    e.g. assets/sport/bed/sport_bed.mp3 -> assets/sport/bed/sport_bed_<stamp>.mp3
    """
    out_dir, base = os.path.split(_abs(asset["path"]))
    stem, ext = os.path.splitext(base)
    suffix = stamp if idx == 0 else "%s-%d" % (stamp, idx)
    return os.path.join(out_dir, "%s_%s%s" % (stem, suffix, ext))


def _have_key():
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return True
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("GEMINI_API_KEY="):
                    v = line.partition("=")[2].strip().strip('"').strip("'")
                    if v:
                        os.environ.setdefault("GEMINI_API_KEY", v)
                        return True
    except FileNotFoundError:
        pass
    return False


def _short_instrumental(src, dst, secs):
    """Trim a long Lyria clip to a short, broadcast-leveled sting with fades."""
    secs = float(secs)
    fade_out = max(secs - 0.4, 0.1)
    af = ("afade=t=in:st=0:d=0.12,"
          "afade=t=out:st=%.3f:d=0.4,"
          "loudnorm=I=%.1f:TP=-1.5:LRA=11" % (fade_out, STING_LUFS))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
           "-t", "%.3f" % secs, "-af", af,
           "-ar", "44100", "-ac", "2",
           "-codec:a", "libmp3lame", "-qscale:a", "2", dst]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        sys.stderr.write(p.stderr.decode("utf-8", "replace"))
        return None
    return dst


def _replace_scope(slug, kinds):
    """Back up & remove the EXISTING variants for (slug, each kind) so only the
    freshly generated one will remain. Used by --replace."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(ROOT, "assets", "_backup_%s" % stamp)
    moved = 0
    for kind in kinds:
        for v in assets_lib.variants(slug, kind):
            src = v["path"]
            dst = os.path.join(backup_dir, _rel(src))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.move(src, dst)
                moved += 1
            except OSError as e:
                sys.stderr.write("  could not back up %s: %s\n" % (src, e))
        assets_lib.clear_default(slug, kind)
    if moved:
        print("  backed up & removed %d existing variant(s) -> %s"
              % (moved, _rel(backup_dir)))
    return moved


# --------------------------------------------------------------------------- #
#  Generators (each returns the new variant path or None)
# --------------------------------------------------------------------------- #
def _gen_bed(asset, voice, tmp, stamp):
    out = _variant_path(asset, stamp)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print("  bed   %s" % _rel(out))
    got = lyria_music.generate(asset["music"], out, instrumental=True)
    return out if got else None


def _gen_sting(asset, voice, tmp, stamp, idx=0):
    out = _variant_path(asset, stamp, idx)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print("  %-5s %s" % (asset["kind"], _rel(out)))
    raw = os.path.join(tmp, "raw_%s" % os.path.basename(out))
    got = lyria_music.generate(asset["music"], raw, instrumental=True)
    if not got:
        return None
    return out if _short_instrumental(raw, out, asset.get("secs", 2.8)) else None


def _latest_bed(slug):
    """Newest bed variant for a show (to mix voiced IDs over), or None."""
    vs = assets_lib.variants(slug, "bed")
    return vs[0]["path"] if vs else None


def _gen_voiced(asset, voice, tmp, slug, stamp):
    """Speak the Arabic script and mix it under this show's freshest bed."""
    out = _variant_path(asset, stamp)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print("  %-5s %s  (voice: %s)" % (asset["kind"], _rel(out), voice))
    voice_wav = os.path.join(tmp, "voice_%s.wav" % os.path.basename(out))
    try:
        tts_engine.synthesize(asset["script"], voice_wav, voice=voice)
    except SystemExit as e:
        sys.stderr.write("  TTS failed: %s\n" % e)
        return None
    bed = _latest_bed(slug)
    audio_mix.build(voice_wav, out, bed=bed, intro=None, outro=None)
    return out


def regenerate(set_slugs, kinds, voice_override, dry_run, make_default):
    ok, fail = 0, 0
    for slug in set_slugs:
        cfg = SETS[slug]
        voice = voice_override or cfg.get("voice") or "Kore"
        assets = [a for a in cfg["assets"] if a["kind"] in kinds]
        if not assets:
            continue
        print("\n== %s ==" % slug)
        tmp = tempfile.mkdtemp(prefix="imaging_%s_" % slug)
        # bed first (voiced intros/outros mix over the newest bed), then rest.
        assets = sorted(assets, key=lambda a: KIND_ORDER.get(a["kind"], 9))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        sting_idx = 0
        for a in assets:
            if dry_run:
                print("  would generate %-5s -> %s"
                      % (a["kind"], _rel(_variant_path(a, stamp, sting_idx))))
                if a["kind"] == "sting":
                    sting_idx += 1
                continue
            try:
                if a["kind"] == "bed":
                    newp = _gen_bed(a, voice, tmp, stamp)
                elif a["kind"] == "transition":
                    newp = _gen_sting(a, voice, tmp, stamp)
                elif a["kind"] == "sting":
                    newp = _gen_sting(a, voice, tmp, stamp, sting_idx)
                    sting_idx += 1
                else:  # intro / outro
                    newp = _gen_voiced(a, voice, tmp, slug, stamp)
            except Exception as e:  # noqa: BLE001
                sys.stderr.write("  error on %s: %s\n" % (a["path"], e))
                newp = None
            if newp:
                ok += 1
                if make_default:
                    try:
                        assets_lib.set_default(slug, a["kind"],
                                               os.path.basename(newp))
                        print("      \u2605 pinned as default %s/%s"
                              % (slug, a["kind"]))
                    except Exception as e:  # noqa: BLE001
                        sys.stderr.write("  could not pin default: %s\n" % e)
            else:
                fail += 1
                print("  \u2717 failed: %s" % a["path"])
    return ok, fail


def print_plan(set_slugs, kinds):
    total = 0
    for slug in set_slugs:
        assets = [a for a in SETS[slug]["assets"] if a["kind"] in kinds]
        if not assets:
            continue
        print("\n== %s ==  (voice: %s)" % (slug, SETS[slug].get("voice")))
        for a in assets:
            total += 1
            how = "Lyria 3" if a["kind"] in ("bed", "transition", "sting") \
                else "Gemini TTS + bed"
            existing = len(assets_lib.variants(slug, a["kind"]))
            print("  %-11s %-46s [%s]  (%d existing)"
                  % (a["kind"], a["path"], how, existing))
    print("\n%d new variant(s) would be generated across %d show(s)."
          % (total, len(set_slugs)))


def main():
    ap = argparse.ArgumentParser(
        description="Generate Bouzid FM imaging variants with Google")
    ap.add_argument("--show", "--set", dest="sets", action="append",
                    choices=sorted(SETS.keys()),
                    help="limit to this show (repeatable); default: all")
    ap.add_argument("--kind", dest="kinds", action="append",
                    choices=KINDS,
                    help="limit to this kind (repeatable); default: all")
    ap.add_argument("--list", action="store_true",
                    help="print the plan and exit (changes nothing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the steps, but skip the Gemini calls")
    ap.add_argument("--voice", default=None,
                    help="override the Gemini TTS voice for every set")
    ap.add_argument("--replace", action="store_true",
                    help="back up & remove existing variants in scope first")
    ap.add_argument("--make-default", action="store_true",
                    help="pin each freshly generated variant as the default")
    args = ap.parse_args()

    slugs = args.sets or list(SETS.keys())
    kinds = args.kinds or list(KINDS)

    if args.list:
        print_plan(slugs, kinds)
        return 0

    print("Bouzid FM imaging generator (Google: Lyria 3 + Gemini TTS)")
    print("Variant model: a new timestamped file per run; the app uses the "
          "newest unless you pin a default.")
    print_plan(slugs, kinds)

    if lyria_music is None and any(
            a["kind"] in ("bed", "transition", "sting")
            for s in slugs for a in SETS[s]["assets"] if a["kind"] in kinds):
        print("\n\u2717 lyria_music module unavailable \u2014 cannot generate "
              "beds/stings.")
        return 1
    if not _have_key():
        print("\n\u2717 GEMINI_API_KEY is not set. Add it to .env (a PAID key "
              "is required \u2014 Lyria 3 is not on the free tier).")
        return 1

    if args.replace and not args.dry_run:
        print("\nReplacing: backing up the existing variants in scope \u2026")
        for slug in slugs:
            _replace_scope(slug, kinds)

    if args.dry_run:
        print("\n(dry run: no audio generated, nothing changed)")
        regenerate(slugs, kinds, args.voice, True, False)
        return 0

    ok, fail = regenerate(slugs, kinds, args.voice, False, args.make_default)

    print("\n\u2713 done: %d new variant(s) generated, %d failed." % (ok, fail))
    if fail:
        print("  Some assets failed. Existing variants are untouched (and any "
              "--replace backups are in assets/_backup_*).")
        return 1
    print("  The app now uses the freshest variant for each (show, kind) "
          "automatically \u2014 or pin a favourite in the Imaging tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
