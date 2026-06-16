#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed shows/ with one preset JSON per Bouzid FM talk show.

Run once after install / upgrade:

    python3 scripts/seed_presets.py          # write only MISSING presets (safe)
    python3 scripts/seed_presets.py --force  # overwrite with shipped defaults
    python3 scripts/seed_presets.py --list   # list what is defined here

By default existing presets are left untouched, so your edits in shows/*.json
are never clobbered. These dicts are just the shipped starting point — the real
source of truth is the JSON on disk. See shows/_SCHEMA.md.
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHOWS = os.path.join(ROOT, "shows")

STINGER_MIX = {"intro": False, "outro": False, "bed": True, "transition": False}
FULL_MIX = {"intro": True, "outro": True, "bed": True, "transition": True}

# English UI labels (TUI dropdown + `--list`). The on-air script stays Arabic;
# only the navigation/labels are English so the panel is readable.
TITLE_EN = {
    "farmer": "Farmer's Brief",
    "prayer": "Prayer Times",
    "sport": "Sport in a Minute",
    "health": "Health Tip",
    "proverb": "Proverb of the Day",
    "folktale": "Tunisian Folktale",
    "figures": "Local Figures",
    "word": "Word & Origin",
    "budget": "Budget Recipe",
    "home": "Home Tip",
    "family": "Family Corner",
    "songstory": "Song Story",
    "heritagemusic": "Heritage Music",
    "quiz": "Daily Quiz",
    "didyouknow": "Did You Know?",
    "horoscope": "Daily Horoscope",
    "ramadan": "Ramadan Companion",
    "events": "Regional Agenda",
}


PRESETS = [
    # ----------------------------- daily / news -------------------------- #
    {
        "slug": "farmer",
        "name": "نشرة الفلاحة",
        "emoji": "\U0001F33E",
        "desc": "Daily farmer's brief: weather, market prices, a seasonal tip.",
        "length_min": 2,
        "feed": "weather",
        "upload_folder": "Farmer",
        "mix": FULL_MIX,
        "markers": [
            {"id": "طقس", "display": "\U0001F326\uFE0F الطقس", "hint": "حالة الطقس اليوم من المعطيات الحيّة"},
            {"id": "سوق", "display": "\U0001F4B0 السوق", "hint": "أسعار تقريبية للخضر والغلال والمواشي"},
            {"id": "نصيحة", "display": "\U0001F331 نصيحة فلاحية", "hint": "نصيحة عملية حسب الموسم"},
        ],
        "prompt": (
            "إنت مذيع نشرة فلاحية في إذاعة بوزيد إف إم، موجّهة لفلاحة سيدي بوزيد. "
            "اكتب نشرة قصيرة (حوالي دقيقتين) بالدارجة التونسية ليوم {date}. "
            "ابدأ بالطقس من المعطيات الحيّة، وبعدها أسعار السوق بصفة تقريبية ووضّح أنها تقديرات، "
            "واختم بنصيحة فلاحية عملية تناسب الموسم. الأسلوب محكي وبسيط ومفيد، بدون رموز ولا قوائم."
        ),
        "fallback": [
            {"id": "طقس", "text": "صباح الخير يا فلاحة سيدي بوزيد! الجو اليوم معتدل عموماً، وينصح بمتابعة التوقعات قبل أي عملية سقي أو حصاد."},
            {"id": "سوق", "text": "في السوق، الخضر والغلال الموسمية تبقى الأفضل بيعاً، والأسعار تتبدل حسب العرض — تابعوا سوق الأسبوع."},
            {"id": "نصيحة", "text": "نصيحة اليوم: نظّموا السقي في الصباح الباكر أو العشية باش تربحوا في الماء. بالتوفيق!"},
        ],
    },
    {
        "slug": "prayer",
        "name": "مواقيت الصلاة وتذكير",
        "emoji": "\U0001F54C",
        "desc": "Daily prayer times for Sidi Bouzid + a short reflection.",
        "length_min": 1,
        "feed": "prayer",
        "upload_folder": "Prayer",
        "mix": {"intro": False, "outro": False, "bed": True, "transition": False},
        "markers": [
            {"id": "مواقيت", "display": "\U0001F54C المواقيت", "hint": "أعلن المواقيت من المعطيات الحيّة بدون تغيير"},
            {"id": "تذكير", "display": "\U0001F932 تذكير", "hint": "تذكير روحي قصير ولطيف ودعاء"},
        ],
        "prompt": (
            "إنت مقدّم ركن ديني هادئ في إذاعة بوزيد إف إم. بالعربية الفصحى البسيطة، "
            "أعلن مواقيت الصلاة ليوم {date} من المعطيات الحيّة كما هي بدون تغيير، "
            "ثم أضف تذكيراً روحياً قصيراً ولطيفاً ودعاءً جميلاً. حافظ على الاحترام والبساطة."
        ),
        "fallback": [
            {"id": "مواقيت", "text": "أهلاً بكم. تعذّر جلب المواقيت الدقيقة الآن، يرجى الرجوع للرزنامة المحلية لمواقيت الصلاة في سيدي بوزيد."},
            {"id": "تذكير", "text": "تذكير اليوم: ابدأ يومك بنيّة طيبة وابتسامة، ولا تنس أن تسأل على جارك. رزقاً طيباً للجميع."},
        ],
    },
    {
        "slug": "sport",
        "name": "الرياضة في دقيقة",
        "emoji": "\u26BD",
        "desc": "One-minute Tunisian sports headlines.",
        "length_min": 1,
        "upload_folder": "Sport",
        "mix": STINGER_MIX,
        "markers": [{"id": "رياضة", "display": "\u26BD الرياضة في دقيقة"}],
        "prompt": (
            "إنت معلّق رياضي في إذاعة بوزيد إف إم. بالدارجة التونسية، لخّص في حوالي دقيقة أهم عناوين "
            "الرياضة التونسية (البطولة الوطنية والمنتخب) بأسلوب حماسي. "
            "ما تخترعش نتائج ولا أرقام؛ إذا ما عندكش نتيجة محددة احكي بشكل عام وركّز على الأجواء."
        ),
        "fallback": [
            {"id": "رياضة", "text": "أهلا بيكم في الرياضة في دقيقة! البطولة التونسية دايما مليانة حماس، والجماهير ورا فرقها. تابعوا معانا آخر الأخبار وديما رياضة نظيفة!"},
        ],
    },
    {
        "slug": "health",
        "name": "نصيحة صحية",
        "emoji": "\U0001FA7A",
        "desc": "A short, seasonal health tip (general advice, not medical).",
        "length_min": 1,
        "upload_folder": "Health",
        "mix": STINGER_MIX,
        "markers": [{"id": "صحة", "display": "\U0001FA7A نصيحة صحية"}],
        "prompt": (
            "إنت مقدّم ركن صحي ودود في إذاعة بوزيد إف إم. بالدارجة التونسية، أعط نصيحة صحية عملية "
            "وموسمية (الحرّ، البرد، رمضان…) في حوالي دقيقة. "
            "ما تعطيش وصفات دوائية، نصائح عامة سليمة برك، وادعُ لزيارة الطبيب عند الحاجة."
        ),
        "fallback": [
            {"id": "صحة", "text": "نصيحة اليوم: اشربوا ماء بارشا على مدار النهار، وكلوا خضر وغلال موسمية، وتحرّكوا شوية كل يوم. وإذا حسيتوا بأي عارض، ما تترددوش في زيارة الطبيب."},
        ],
    },
    # ------------------------------- culture ----------------------------- #
    {
        "slug": "proverb",
        "name": "مثل اليوم",
        "emoji": "\U0001F9E0",
        "desc": "A Tunisian proverb, its meaning, and a little story.",
        "length_min": 1,
        "upload_folder": "Proverb",
        "mix": STINGER_MIX,
        "markers": [{"id": "مثل", "display": "\U0001F9E0 مثل اليوم"}],
        "prompt": (
            "إنت راوي في إذاعة بوزيد إف إم. بالدارجة التونسية، قدّم مثلاً تونسياً شعبياً واحداً، "
            "اشرح معناه وفي أي وقت يتقال، وزيد حكاية صغيرة طريفة تخلّيه يعلق في البال. حوالي دقيقة."
        ),
        "fallback": [
            {"id": "مثل", "text": "مثل اليوم يقول: «اللي فات مات». معناه بلّي اللي فات خلّيه واركّز على اليوم وغدوة. تونس عندها حكمة في كل كلمة!"},
        ],
    },
    {
        "slug": "folktale",
        "name": "حكاية تونسية",
        "emoji": "\U0001F4D6",
        "desc": "A short traditional Tunisian/Maghrebi folk tale.",
        "length_min": 3,
        "upload_folder": "Folktales",
        "mix": FULL_MIX,
        "markers": [{"id": "حكاية", "display": "\U0001F4D6 حكاية تونسية"}],
        "prompt": (
            "إنت حكواتي في إذاعة بوزيد إف إم. بالدارجة التونسية وبأسلوب شيّق، احكِ حكاية تونسية أو "
            "مغاربية تراثية قصيرة (حوالي 3 دقائق): بداية تشدّ السامع، عقدة، ونهاية فيها عبرة لطيفة. "
            "ابدأ بـ «يحكي أنّو…» واختم بسلام دافئ."
        ),
        "fallback": [
            {"id": "حكاية", "text": "يحكي أنّو في قديم الزمان، كان فمّا فلاح بسيط يحب أرضو أكثر من روحو. خدم بصبر وعرق، وفي الأخر لقى خير بلادو في غلتو. والعبرة: اللي يزرع بالخير يحصد الخير. ودايما مع حكاياتنا، سلام!"},
        ],
    },
    {
        "slug": "figures",
        "name": "أعلام من الجهة",
        "emoji": "\U0001F31F",
        "desc": "Profiles of notable people from Sidi Bouzid & central Tunisia.",
        "length_min": 2,
        "upload_folder": "Figures",
        "mix": FULL_MIX,
        "markers": [{"id": "علم", "display": "\U0001F31F أعلام من الجهة"}],
        "prompt": (
            "إنت مقدّم ركن ثقافي في إذاعة بوزيد إف إم. بالدارجة التونسية، قدّم شخصية بارزة من سيدي بوزيد "
            "أو وسط تونس (وإن لزم شخصية تونسية ملهمة)، مع نبذة عن حياتها وإنجازها. "
            "استعمل معلومات صحيحة فقط؛ إذا ما كنتش متأكد احكي بشكل عام وما تخترعش تفاصيل."
        ),
        "fallback": [
            {"id": "علم", "text": "سيدي بوزيد ولّادة للرجال والنساء اللي حبّوا بلادهم. من الفلاح للمعلّم للشاب الطموح، كل واحد عندو قصة تستاهل تتحكى. ونتلاقاو مع علم جديد!"},
        ],
    },
    {
        "slug": "word",
        "name": "كلمة وأصلها",
        "emoji": "\U0001F524",
        "desc": "The origin & use of a Tunisian-dialect word.",
        "length_min": 1,
        "upload_folder": "Word",
        "mix": STINGER_MIX,
        "markers": [{"id": "كلمة", "display": "\U0001F524 كلمة وأصلها"}],
        "prompt": (
            "إنت مقدّم ركن لغوي خفيف في إذاعة بوزيد إف إم. بالدارجة التونسية، اختر كلمة من الدارجة "
            "التونسية، احكي على أصلها ومعناها وكيفاش تتستعمل بأمثلة طريفة. حوالي دقيقة."
        ),
        "fallback": [
            {"id": "كلمة", "text": "كلمة اليوم: «ياسر»، ومعناها بارشا ولّا كثير. نقولوا «ياسر شكر». كلمة خفيفة وحلوة نستعملوها بارشا في تونس!"},
        ],
    },
    # ------------------------------ lifestyle ---------------------------- #
    {
        "slug": "budget",
        "name": "وصفة اقتصادية",
        "emoji": "\U0001F373",
        "desc": "A cheap, seasonal, local-ingredient recipe.",
        "length_min": 2,
        "upload_folder": "Budget",
        "mix": FULL_MIX,
        "markers": [
            {"id": "وصفة", "display": "\U0001F373 الوصفة", "hint": "المقادير البسيطة المتوفرة"},
            {"id": "خطوات", "display": "\U0001F4DD الخطوات", "hint": "خطوات التحضير بإيجاز"},
        ],
        "prompt": (
            "إنت طبّاخ ودود في إذاعة بوزيد إف إم. بالدارجة التونسية، قدّم وصفة اقتصادية بسيطة "
            "بمكوّنات محلية وموسمية متوفرة في سيدي بوزيد: المقادير الأول، وبعدها الخطوات بإيجاز. خلّيها سهلة ومغرية."
        ),
        "fallback": [
            {"id": "وصفة", "text": "اليوم عندنا عجّة بالخضرة الرخيصة واللذيذة: تحتاج بصلة، طماطم، فلفل، بيض، وشوية زيت زيتون."},
            {"id": "خطوات", "text": "ريّب الخضرة في الزيت، زيد البيض المخفوق وملح وبهار، واخدمها على نار هادية حتى تشدّ. صحّة وهنا!"},
        ],
    },
    {
        "slug": "home",
        "name": "نصيحة البيت",
        "emoji": "\U0001F3E0",
        "desc": "Practical home & garden / money-saving tip.",
        "length_min": 1,
        "upload_folder": "Home",
        "mix": STINGER_MIX,
        "markers": [{"id": "نصيحة", "display": "\U0001F3E0 نصيحة البيت"}],
        "prompt": (
            "إنت مقدّم ركن للبيت في إذاعة بوزيد إف إم. بالدارجة التونسية، أعط نصيحة منزلية أو فلاحية "
            "عملية ومفيدة (تنظيم البيت، الحديقة، توفير الفلوس والطاقة). حوالي دقيقة."
        ),
        "fallback": [
            {"id": "نصيحة", "text": "نصيحة اليوم: خبّي الخضرة الورقية في فوطة رطبة في الثلاجة باش تدوم أطول وما تضيعش. توفير صغير يعمل فرق!"},
        ],
    },
    {
        "slug": "family",
        "name": "ركن الأسرة",
        "emoji": "\U0001F46A",
        "desc": "Warm advice on parenting, relationships, wellbeing.",
        "length_min": 2,
        "upload_folder": "Family",
        "mix": FULL_MIX,
        "markers": [{"id": "أسرة", "display": "\U0001F46A ركن الأسرة"}],
        "prompt": (
            "إنت مقدّم ركن للأسرة في إذاعة بوزيد إف إم. بالدارجة التونسية وبأسلوب دافئ ومحترم، "
            "قدّم نصيحة في التربية أو العلاقات العائلية أو الصحة النفسية، بأمثلة من الحياة اليومية. حوالي دقيقتين."
        ),
        "fallback": [
            {"id": "أسرة", "text": "في ركن الأسرة، نفكّروا أن أحلى هدية لأولادنا هي الوقت. اقعدوا معاهم، اسمعوا ليهم، واحكيوا معاهم. العائلة اللي تتحادث تبقى قوية. دومتم بخير!"},
        ],
    },
    # --------------------------- music & entertainment ------------------- #
    {
        "slug": "songstory",
        "name": "الأغنية وقصتها",
        "emoji": "\U0001F3B5",
        "desc": "The backstory of a well-known Tunisian/Maghrebi song.",
        "length_min": 2,
        "upload_folder": "SongStory",
        "mix": FULL_MIX,
        "markers": [{"id": "أغنية", "display": "\U0001F3B5 الأغنية وقصتها"}],
        "prompt": (
            "إنت مقدّم ركن موسيقي في إذاعة بوزيد إف إم. بالدارجة التونسية، احكي قصة أغنية تونسية أو "
            "مغاربية معروفة وسياقها وصاحبها، بدون ما تشغّل الأغنية (المنشّط يشغّلها بعد). "
            "استعمل معلومات صحيحة فقط؛ إذا ما كنتش متأكد احكي على النوع الموسيقي بشكل عام."
        ),
        "fallback": [
            {"id": "أغنية", "text": "الموسيقى التونسية عندها تاريخ غني، من المالوف للمزود للأغاني العصرية. كل أغنية وراها قصة ومشاعر. توّا نسمعوا وصلة حلوة!"},
        ],
    },
    {
        "slug": "heritagemusic",
        "name": "مزود وتراث",
        "emoji": "\U0001FA98",
        "desc": "Spoken intro to a traditional Tunisian music set.",
        "length_min": 1,
        "upload_folder": "HeritageMusic",
        "mix": STINGER_MIX,
        "markers": [{"id": "تراث", "display": "\U0001FA98 مزود وتراث"}],
        "prompt": (
            "إنت مقدّم ركن المزود والتراث الموسيقي التونسي في إذاعة بوزيد إف إم. بالدارجة التونسية، "
            "اعمل تقديم دافئ وقصير (حوالي دقيقة) على المزود والتراث قبل وصلة موسيقية، وحمّس السامع للاستماع."
        ),
        "fallback": [
            {"id": "تراث", "text": "أهلا بيكم في ركن المزود والتراث! المزود هو نبض الفرح في أعراسنا وأعيادنا. شدّوا العزيمة، وخلّيوا الإيقاع ياخذكم. واو للوصلة الجاية!"},
        ],
    },
    # ----------------------------- engagement ---------------------------- #
    {
        "slug": "quiz",
        "name": "سؤال اليوم",
        "emoji": "\u2753",
        "desc": "A fun daily quiz teaser (answer revealed later).",
        "length_min": 1,
        "upload_folder": "Quiz",
        "mix": STINGER_MIX,
        "markers": [
            {"id": "سؤال", "display": "\u2753 سؤال اليوم"},
            {"id": "تلميح", "display": "\U0001F4AD تلميح", "hint": "تلميح بسيط بدون كشف الإجابة"},
        ],
        "prompt": (
            "إنت منشّط مسابقة في إذاعة بوزيد إف إم. بالدارجة التونسية، اطرح سؤالاً ثقافياً أو تاريخياً "
            "ممتعاً على تونس أو سيدي بوزيد، وزيد تلميح بسيط، بدون ما تكشف الإجابة (تنعطى في حلقة جاية). "
            "حمّس السامع باش يفكّر ويتابع."
        ),
        "fallback": [
            {"id": "سؤال", "text": "سؤال اليوم: في أي سنة بدات الأحداث اللي خلّت سيدي بوزيد معروفة في العالم كلّو؟"},
            {"id": "تلميح", "text": "التلميح: كانت في آخر شهر من العام. فكّروا شوية، والإجابة غدوة!"},
        ],
    },
    {
        "slug": "didyouknow",
        "name": "هل تعلم؟",
        "emoji": "\U0001F4A1",
        "desc": "A short, surprising 'did you know' fact.",
        "length_min": 1,
        "upload_folder": "DidYouKnow",
        "mix": STINGER_MIX,
        "markers": [{"id": "حقيقة", "display": "\U0001F4A1 هل تعلم؟"}],
        "prompt": (
            "إنت مقدّم فقرة «هل تعلم؟» في إذاعة بوزيد إف إم. بالدارجة التونسية، قدّم معلومة قصيرة "
            "ومثيرة (علم، تاريخ، تونس، طبيعة) في جملتين أو ثلاثة. المعلومة لازم تكون صحيحة ودقيقة."
        ),
        "fallback": [
            {"id": "حقيقة", "text": "هل تعلم؟ أن تونس فيها أكثر من ثمانية مواقع مصنّفة على قائمة التراث العالمي لليونسكو؟ بلادنا صغيرة أمّا تاريخها كبير ياسر!"},
        ],
    },
    {
        "slug": "horoscope",
        "name": "برج اليوم",
        "emoji": "\U0001F52E",
        "desc": "A light, positive daily horoscope (for fun).",
        "length_min": 1,
        "upload_folder": "Horoscope",
        "mix": STINGER_MIX,
        "markers": [{"id": "برج", "display": "\U0001F52E برج اليوم"}],
        "prompt": (
            "إنت مقدّم فقرة أبراج ترفيهية في إذاعة بوزيد إف إم. بالدارجة التونسية، قدّم توقّع أبراج خفيف "
            "وإيجابي ليوم {date} (لمحة عامة أو لبرج واحد)، بأسلوب مرح، ونبّه في الأخر أنه للتسلية فقط."
        ),
        "fallback": [
            {"id": "برج", "text": "برج اليوم: طاقتك إيجابية اليوم، استغلّها باش تبدأ حاجة كنت مؤجّلها. ابتسم واعمل خير. وتذكّروا — الأبراج للتسلية برك!"},
        ],
    },
    # ------------------------------ seasonal ----------------------------- #
    {
        "slug": "ramadan",
        "name": "رفيق رمضان",
        "emoji": "\U0001F319",
        "desc": "Ramadan companion: imsak/iftar, a reflection, a light recipe.",
        "length_min": 2,
        "feed": "prayer",
        "upload_folder": "Ramadan",
        "mix": FULL_MIX,
        "markers": [
            {"id": "مواقيت", "display": "\U0001F319 إمساك وإفطار", "hint": "الإمساك (الفجر) والإفطار (المغرب) من المعطيات"},
            {"id": "تأمل", "display": "\U0001F932 تأمل اليوم", "hint": "تأمل روحي قصير"},
            {"id": "وصفة", "display": "\U0001F372 وصفة الإفطار", "hint": "وصفة إفطار خفيفة"},
        ],
        "prompt": (
            "إنت رفيق رمضان في إذاعة بوزيد إف إم. بالدارجة التونسية، أعلن وقت الإمساك (الفجر) والإفطار "
            "(المغرب) من المعطيات الحيّة، ثم أضف تأمّلاً قصيراً ودافئاً، واختم بوصفة إفطار خفيفة بمكوّنات محلية."
        ),
        "fallback": [
            {"id": "مواقيت", "text": "رمضان كريم! تعذّر جلب المواقيت الدقيقة الآن، تابعوا رزنامة سيدي بوزيد لوقت الإمساك والإفطار."},
            {"id": "تأمل", "text": "تأمل اليوم: رمضان فرصة باش نصفّيوا قلوبنا ونوصلوا أرحامنا ونعاونوا اللي محتاج."},
            {"id": "وصفة", "text": "وصفة الإفطار: شوربة فريك ساخنة بالخضرة ترجّع الروح للجسم بعد يوم صيام. صحّة وشربة هنية!"},
        ],
    },
    {
        "slug": "events",
        "name": "روزنامة الجهة",
        "emoji": "\U0001F4C5",
        "desc": "Weekly markets, festivals & local announcements for the region.",
        "length_min": 1,
        "upload_folder": "Events",
        "mix": STINGER_MIX,
        "markers": [{"id": "روزنامة", "display": "\U0001F4C5 روزنامة الجهة"}],
        "prompt": (
            "إنت مقدّم ركن المواعيد في إذاعة بوزيد إف إم. بالدارجة التونسية، قدّم لمحة عن المواعيد "
            "الأسبوعية في سيدي بوزيد (سوق الأسبوع، تظاهرات ثقافية، إعلانات مدنية) بشكل عام إذا ما توفّرتش تفاصيل محددة، بدون اختراع مواعيد."
        ),
        "fallback": [
            {"id": "روزنامة", "text": "في روزنامة الجهة: سوق الأسبوع دايما موعد يجمع الناس والخير، والتظاهرات الثقافية تحيي الجهة. تابعوا الإعلانات المحلية باش ما يفوتكمش شي."},
        ],
    },
]


def main():
    ap = argparse.ArgumentParser(description="Seed Bouzid FM show presets")
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing presets with shipped defaults")
    ap.add_argument("--list", action="store_true",
                    help="list the presets defined here and exit")
    args = ap.parse_args()

    # Make sure every preset carries an English UI label.
    for p in PRESETS:
        p.setdefault("title_en", TITLE_EN.get(p["slug"], p["slug"]))

    if args.list:
        for p in PRESETS:
            print("  %-14s %s %s" % (p["slug"], p.get("emoji", ""),
                                     p.get("title_en", p["slug"])))
        print("\n%d shows defined." % len(PRESETS))
        return

    os.makedirs(SHOWS, exist_ok=True)
    wrote = skipped = 0
    for p in PRESETS:
        path = os.path.join(SHOWS, p["slug"] + ".json")
        if os.path.exists(path) and not args.force:
            skipped += 1
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
            f.write("\n")
        wrote += 1
        print("wrote shows/%s.json" % p["slug"])
    print("\nDone: %d written, %d left untouched. Total defined: %d."
          % (wrote, skipped, len(PRESETS)))
    if skipped and not args.force:
        print("(use --force to overwrite existing presets)")


if __name__ == "__main__":
    main()
