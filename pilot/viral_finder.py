#!/usr/bin/env python3
"""viral_finder.py - Reddit Rising Posts Velocity Scanner & Subreddit Intelligence.

Features:
1. 65-Subreddit Curated Database (from Sheet 3): member counts, karma types, restrictions, tips.
2. Rising Velocity Scanner: detects high-potential viral candidate posts in the Golden Window.
3. Post Context Extractor: fetches post details and existing top comments for tone analysis.
"""
import json
import re
import sys
import time
import urllib.parse
from typing import Any, Dict, List, Optional

# Embedded 65-Subreddit Playbook from Sheet 3
CURATED_SUBREDDITS = [
    {"sub": "CasualConversation", "members": "~3M", "cat": "Casual chat", "karma": "Comment", "restrictions": "No venting or relationship-advice posts", "tip": "Reply to 2-3 threads a day with a genuine personal take; short warm replies do well."},
    {"sub": "AskReddit", "members": "~50M", "cat": "Casual chat", "karma": "Comment", "restrictions": "Huge competition; posts need strong questions to surface", "tip": "Sort by Rising and answer new questions with a real personal anecdote."},
    {"sub": "NoStupidQuestions", "members": "~4M", "cat": "Casual chat", "karma": "Comment", "restrictions": "Be kind; no trolling or mocking questions", "tip": "Answer questions you genuinely know about; short helpful answers get upvoted."},
    {"sub": "TrueOffMyChest", "members": "~3M", "cat": "Casual chat", "karma": "Both", "restrictions": "No advice-seeking; must be a personal story", "tip": "Share an honest life story; long genuine posts can blow up."},
    {"sub": "CasualUK", "members": "~900k", "cat": "Casual chat", "karma": "Comment", "restrictions": "UK-flavoured humour; keep it light", "tip": "Join daily threads with dry wit; Brits reward it."},
    {"sub": "SeriousConversation", "members": "~150k", "cat": "Casual chat", "karma": "Comment", "restrictions": "Civil, thoughtful replies expected; low-effort removed", "tip": "Write one thoughtful paragraph, not one-liners; quality over speed."},
    {"sub": "MakeNewFriendsHere", "members": "~200k", "cat": "Casual chat", "karma": "Comment", "restrictions": "Read rules; no personal info in public posts", "tip": "Introduce yourself in the weekly thread; reply to others first."},
    {"sub": "self", "members": "~1M", "cat": "Casual chat", "karma": "Both", "restrictions": "Story-driven; low-effort posts removed", "tip": "Post a real story from your week; leave empathetic comments on others."},
    {"sub": "explainlikeimfive", "members": "~23M", "cat": "Q&A / advice", "karma": "Comment", "restrictions": "Top-level comments must attempt an explanation; jokes removed", "tip": "Give a clear simple answer; good analogies earn big upvotes."},
    {"sub": "AskMen", "members": "~6M", "cat": "Q&A / advice", "karma": "Comment", "restrictions": "Follow sub rules; no soapboxing", "tip": "Answer honestly from your own perspective; short direct answers work."},
    {"sub": "AskWomen", "members": "~6M", "cat": "Q&A / advice", "karma": "Comment", "restrictions": "Respectful tone required; read the rules", "tip": "Thoughtful, respectful replies rise to the top."},
    {"sub": "socialskills", "members": "~3M", "cat": "Q&A / advice", "karma": "Comment", "restrictions": "Be supportive; no mocking people", "tip": "Share what actually worked for you; practical tips win."},
    {"sub": "DecidingToBeBetter", "members": "~900k", "cat": "Q&A / advice", "karma": "Comment", "restrictions": "Supportive only; no shaming", "tip": "Encourage others and share small wins of your own."},
    {"sub": "getdisciplined", "members": "~2M", "cat": "Q&A / advice", "karma": "Comment", "restrictions": "No-nonsense mods; low-effort advice removed", "tip": "Give concrete actionable advice; mention your own routine."},
    {"sub": "LifeProTips", "members": "~25M", "cat": "Q&A / advice", "karma": "Both", "restrictions": "Reposts removed; tips must be genuinely useful", "tip": "Post one original practical tip; check it is not a repost first."},
    {"sub": "aww", "members": "~36M", "cat": "Pets / animals", "karma": "Post", "restrictions": "Cute animals only; no text screenshots", "tip": "Post a cute pet photo with a simple title; US mornings do best."},
    {"sub": "cats", "members": "~6M", "cat": "Pets / animals", "karma": "Post", "restrictions": "Cat content only", "tip": "Share a nice cat photo; comment on other people's cats too."},
    {"sub": "dogs", "members": "~3.5M", "cat": "Pets / animals", "karma": "Post", "restrictions": "Dog content only", "tip": "Genuine captions help; engage with other dog posts."},
    {"sub": "WhatsWrongWithYourDog", "members": "~1.5M", "cat": "Pets / animals", "karma": "Post", "restrictions": "Silly captions on dog pics; keep it light", "tip": "Add a funny caption to a goofy dog photo; low bar, fast karma."},
    {"sub": "BeforeNAfterAdoption", "members": "~1.5M", "cat": "Pets / animals", "karma": "Post", "restrictions": "Before/after adoption pictures", "tip": "Wholesome before/after rescue photos always do well."},
    {"sub": "Rabbits", "members": "~600k", "cat": "Pets / animals", "karma": "Both", "restrictions": "Rabbit content; care questions welcome", "tip": "Post bunny photos; answer basic care questions you know."},
    {"sub": "birding", "members": "~1M", "cat": "Pets / animals", "karma": "Both", "restrictions": "Genuine sightings and photos expected", "tip": "Share a bird you spotted; ID-help requests get engagement."},
    {"sub": "shittyfoodporn", "members": "~2.5M", "cat": "Food / cooking", "karma": "Post", "restrictions": "Ugly food welcome; must be real food you show", "tip": "Post an honest ugly meal; humour in the title wins."},
    {"sub": "EatCheapAndHealthy", "members": "~3M", "cat": "Food / cooking", "karma": "Both", "restrictions": "Meals should be cheap and reasonably healthy", "tip": "Share a cheap meal with a cost breakdown; people love numbers."},
    {"sub": "MealPrepSunday", "members": "~3.5M", "cat": "Food / cooking", "karma": "Post", "restrictions": "Prep photos with a description of what is shown", "tip": "Post your weekly prep with a short recipe list."},
    {"sub": "cookingforbeginners", "members": "~3.5M", "cat": "Food / cooking", "karma": "Both", "restrictions": "Beginner-friendly; be encouraging", "tip": "Ask genuine beginner questions or share simple wins."},
    {"sub": "15minutefood", "members": "~1M", "cat": "Food / cooking", "karma": "Post", "restrictions": "Quick meals; keep it honest", "tip": "Post a fast meal you actually cooked; keep titles simple."},
    {"sub": "Baking", "members": "~3.5M", "cat": "Food / cooking", "karma": "Post", "restrictions": "Baked goods; recipes appreciated", "tip": "Share what you baked with a short recipe note."},
    {"sub": "AskCulinary", "members": "~2.5M", "cat": "Food / cooking", "karma": "Comment", "restrictions": "Serious cooking Q&A; no low-effort", "tip": "Answer cooking technique questions you actually know."},
    {"sub": "mildlyinteresting", "members": "~23M", "cat": "Pics / art", "karma": "Post", "restrictions": "Must be genuinely 'mildly' interesting; no memes", "tip": "Snap something odd you see in daily life; simple titles work."},
    {"sub": "MadeMeSmile", "members": "~11M", "cat": "Pics / art", "karma": "Post", "restrictions": "Wholesome only; no sad or ragebait content", "tip": "Share something that genuinely made you smile today."},
    {"sub": "itookapicture", "members": "~4.5M", "cat": "Pics / art", "karma": "Post", "restrictions": "Must be your own photo; no AI images", "tip": "Post your best phone photo; natural shots beat heavy filters."},
    {"sub": "CozyPlaces", "members": "~7M", "cat": "Pics / art", "karma": "Post", "restrictions": "Cozy imagery; original content preferred", "tip": "Share a cozy corner; warm lighting wins."},
    {"sub": "IDAP", "members": "~1M", "cat": "Pics / art", "karma": "Post", "restrictions": "Must be your own drawing", "tip": "Post a sketch at any skill level; beginners get encouraged."},
    {"sub": "learnart", "members": "~1.5M", "cat": "Pics / art", "karma": "Both", "restrictions": "Feedback-focused; welcoming to beginners", "tip": "Ask for critique on a piece; give feedback to others too."},
    {"sub": "analog", "members": "~2M", "cat": "Pics / art", "karma": "Post", "restrictions": "Film photos only; no digital-with-filter shots", "tip": "Only for real film shots; skip this one if you shoot digital."},
    {"sub": "AmateurRoomPorn", "members": "~1.5M", "cat": "Pics / art", "karma": "Post", "restrictions": "Amateur photos only; no professional shots", "tip": "Post your real room; tidy up and use good light."},
    {"sub": "patientgamers", "members": "~2.5M", "cat": "Gaming", "karma": "Comment", "restrictions": "Discussion-focused; no memes or low-effort", "tip": "Discuss a game you finished recently; thoughtful takes rise."},
    {"sub": "ShouldIbuythisgame", "members": "~1M", "cat": "Gaming", "karma": "Comment", "restrictions": "Helpful recommendations expected", "tip": "Recommend games you have actually played and finished."},
    {"sub": "tipofmyjoystick", "members": "~1.5M", "cat": "Gaming", "karma": "Comment", "restrictions": "Suggestions should fit what was asked", "tip": "Match suggestions to the request and explain why."},
    {"sub": "StardewValley", "members": "~2.5M", "cat": "Gaming", "karma": "Both", "restrictions": "Friendly; game-related content", "tip": "Share your farm progress; ask genuine questions."},
    {"sub": "Minecraft", "members": "~8M", "cat": "Gaming", "karma": "Both", "restrictions": "Lenient; builds do especially well", "tip": "Post a build screenshot; comment on others' builds."},
    {"sub": "GirlGamers", "members": "~600k", "cat": "Gaming", "karma": "Comment", "restrictions": "Inclusive; zero tolerance for harassment", "tip": "Join discussions warmly; great for genuine chat."},
    {"sub": "cozygamers", "members": "~1M", "cat": "Gaming", "karma": "Both", "restrictions": "Wholesome gaming chat; low bar", "tip": "Talk about relaxing games you play; easy engagement."},
    {"sub": "wholesomememes", "members": "~10M", "cat": "Memes / humor", "karma": "Post", "restrictions": "Wholesome only; no edgy humour", "tip": "Post kind, uplifting memes; avoid anything edgy."},
    {"sub": "dadjokes", "members": "~2.5M", "cat": "Memes / humor", "karma": "Post", "restrictions": "Needs ~200 karma to post - earn elsewhere first", "tip": "Post your best groaner; original jokes do better than reposts."},
    {"sub": "Jokes", "members": "~3M", "cat": "Memes / humor", "karma": "Post", "restrictions": "Setup and punchline format", "tip": "Post a clean classic joke with a proper setup."},
    {"sub": "ComedyCemetery", "members": "~6M", "cat": "Memes / humor", "karma": "Post", "restrictions": "Post memes that 'died'; read the room first", "tip": "Lurk a little, then post a stale meme with irony."},
    {"sub": "HistoryMemes", "members": "~8M", "cat": "Memes / humor", "karma": "Post", "restrictions": "Must be history-related", "tip": "Pair a history fact you know with a meme format."},
    {"sub": "ProgrammerHumor", "members": "~5M", "cat": "Memes / humor", "karma": "Post", "restrictions": "Programming-related humour", "tip": "Post a relatable dev joke; works best if you code."},
    {"sub": "AdviceAnimals", "members": "~9M", "cat": "Memes / humor", "karma": "Post", "restrictions": "Strict image-macro format with classic templates", "tip": "Use classic macro templates; format matters more than wit."},
    {"sub": "me_irl", "members": "~18M", "cat": "Memes / humor", "karma": "Post", "restrictions": "Self-deprecating; reposts removed aggressively", "tip": "Post a relatable self meme; original content beats reposts."},
    {"sub": "gerbil", "members": "~31k", "cat": "Pets / animals", "karma": "Both", "restrictions": "Small friendly pet community", "tip": "Post gerbil photos; answer basic care questions."},
    {"sub": "rescuecats", "members": "~44k", "cat": "Pets / animals", "karma": "Post", "restrictions": "Posts must be rescue-cat related", "tip": "Share a rescue story; wholesome content does well."},
    {"sub": "pigeons", "members": "~17k", "cat": "Pets / animals", "karma": "Both", "restrictions": "Pigeon-positive space; no animal-abuser content", "tip": "Post pigeon photos; this community loves underrated birds."},
    {"sub": "corydoras", "members": "~39k", "cat": "Pets / animals", "karma": "Both", "restrictions": "Aquarium hobbyists; stay on topic", "tip": "Share tank photos; ask or answer care questions."},
    {"sub": "sheep", "members": "~43k", "cat": "Pets / animals", "karma": "Post", "restrictions": "No gore; wholesome farm content", "tip": "Post sheep photos; simple farm-life captions work."},
    {"sub": "bioactive", "members": "~37k", "cat": "Pets / animals", "karma": "Both", "restrictions": "Hobbyist community; be respectful", "tip": "Share your enclosure setup; ask build questions."},
    {"sub": "Degus", "members": "~9k", "cat": "Pets / animals", "karma": "Both", "restrictions": "Small niche pet community", "tip": "Post degu photos; care questions welcome."},
    {"sub": "Guppies", "members": "~20k", "cat": "Pets / animals", "karma": "Both", "restrictions": "Aquarium hobbyists; small active community", "tip": "Share tank photos; help with beginner questions."},
    {"sub": "BakingInJapan", "members": "~7k", "cat": "Food / cooking", "karma": "Post", "restrictions": "Niche regional baking community", "tip": "Post bakes with local ingredients; small and welcoming."},
    {"sub": "BakingPhilippines", "members": "~43k", "cat": "Food / cooking", "karma": "Post", "restrictions": "No product ads", "tip": "Share bakes; regional twists get love."},
    {"sub": "Goat_Format", "members": "~6k", "cat": "Gaming", "karma": "Both", "restrictions": "Be respectful and welcoming", "tip": "Discuss the retro format; small passionate community."},
    {"sub": "goateeguys", "members": "~46k", "cat": "Lifestyle", "karma": "Post", "restrictions": "Posts must show a goatee", "tip": "Post a well-groomed goatee; light-hearted community."}
]


def list_curated_subreddits(category: Optional[str] = None, karma_type: Optional[str] = None) -> List[Dict]:
    """Filters curated subreddits by category or karma preference."""
    res = []
    for s in CURATED_SUBREDDITS:
        if category and category.lower() not in s["cat"].lower():
            continue
        if karma_type and karma_type.lower() not in s["karma"].lower():
            continue
        res.append(s)
    return res


def get_sub_tip(subreddit_name: str) -> Optional[Dict]:
    """Returns metadata and participation tip for a specific subreddit."""
    clean = re.sub(r"^/?r/", "", subreddit_name.strip()).lower()
    for s in CURATED_SUBREDDITS:
        if s["sub"].lower() == clean:
            return s
    return None


def fetch_rising_posts(csi_post_func, csi_eval_unwrap_func, subreddit: str = "AskReddit", session: str = "pilot", limit: int = 25) -> List[Dict]:
    """Scans /r/{subreddit}/rising/ and calculates viral velocity for each post."""
    clean_sub = re.sub(r"^/?r/", "", subreddit.strip())
    html_url = f"https://www.reddit.com/r/{clean_sub}/rising/"

    # Navigate to rising feed
    csi_post_func("navigate", {"url": html_url, "newTab": True}, session)
    time.sleep(2.0)

    # In-page fetch from reddit origin for maximum reliability
    js_fetch = """(async function() {
        try {
            let resp = await fetch('https://www.reddit.com/r/%s/rising.json?raw_json=1&limit=%d');
            if (resp.status === 200) {
                let d = await resp.json();
                return {status: 200, children: d.data.children.map(c => c.data)};
            }
            return {status: resp.status};
        } catch(e) {
            return {status: 0, error: e.toString()};
        }
    })()""" % (clean_sub, limit)

    tier1 = csi_eval_unwrap_func(js_fetch, session)
    raw_posts = []

    if isinstance(tier1, dict) and tier1.get("status") == 200:
        raw_posts = tier1.get("children", [])

    # Tier 2 DOM Fallback if JSON was restricted
    if not raw_posts:
        time.sleep(1.5)
        js_dom = """(function(){
            var out = [];
            document.querySelectorAll('shreddit-post').forEach(function(p){
                out.push({
                    id: p.getAttribute('id'),
                    title: p.getAttribute('post-title'),
                    author: p.getAttribute('author'),
                    score: parseInt(p.getAttribute('score') || '0', 10),
                    num_comments: parseInt(p.getAttribute('comment-count') || '0', 10),
                    permalink: p.getAttribute('permalink'),
                    created_utc: p.getAttribute('created-timestamp') ? (new Date(p.getAttribute('created-timestamp')).getTime() / 1000) : 0
                });
            });
            return out;
        })()"""
        dom_res = csi_eval_unwrap_func(js_dom, session)
        if isinstance(dom_res, list):
            raw_posts = dom_res

    now = time.time()
    candidates = []

    for p in raw_posts:
        title = p.get("title", "")
        author = p.get("author", "")
        score = int(p.get("score") or 0)
        comments = int(p.get("num_comments") or 0)
        created_utc = float(p.get("created_utc") or now)
        permalink = p.get("permalink", "")
        if not permalink.startswith("http"):
            permalink = f"https://www.reddit.com{permalink}"

        age_min = max(1.0, round((now - created_utc) / 60.0, 1))

        # Velocity metric: (score + 1.5 * comments) / age_minutes
        velocity = round((score + (comments * 1.5)) / age_min, 2)

        # Golden Window: Age 15-90 min, Comments < 50, Early Traction
        is_golden = (15.0 <= age_min <= 120.0) and (comments <= 50) and (score >= 2 or velocity >= 0.3)

        candidates.append({
            "title": title,
            "author": author,
            "score": score,
            "comments": comments,
            "age_minutes": age_min,
            "velocity": velocity,
            "is_golden_window": is_golden,
            "url": permalink,
            "id": p.get("id") or p.get("name")
        })

    # Sort candidates: Golden window first, then by velocity descending
    candidates.sort(key=lambda x: (1 if x["is_golden_window"] else 0, x["velocity"]), reverse=True)
    return candidates


def extract_post_context(csi_post_func, csi_eval_unwrap_func, post_url: str, session: str = "pilot") -> Dict[str, Any]:
    """Extracts post title, full selftext, and top comments for tone/angle analysis."""
    clean_url = post_url.split("?")[0].rstrip("/")
    jurl = clean_url + "/.json?raw_json=1&limit=8"

    csi_post_func("navigate", {"url": clean_url, "newTab": True}, session)
    time.sleep(1.8)

    # In-page fetch from reddit tab
    js_fetch = """(async function(){
        try {
            let resp = await fetch('%s/.json?raw_json=1&limit=8');
            if (resp.status === 200) {
                let d = await resp.json();
                return {status: 200, data: d};
            }
            return {status: resp.status};
        } catch(e) {
            return {status: 0, error: e.toString()};
        }
    })()""" % clean_url

    res = csi_eval_unwrap_func(js_fetch, session)
    if isinstance(res, dict) and res.get("status") == 200:
        d = res.get("data")
        if isinstance(d, list) and len(d) >= 1:
            post_data = d[0]["data"]["children"][0]["data"]
            comments_children = d[1]["data"]["children"] if len(d) > 1 else []
            top_comments = []
            for c in comments_children:
                cd = c.get("data", {})
                if c.get("kind") == "t1" and cd.get("body"):
                    top_comments.append({
                        "author": cd.get("author"),
                        "score": cd.get("score"),
                        "body": cd.get("body")[:300]
                    })
            return {
                "title": post_data.get("title"),
                "author": post_data.get("author"),
                "subreddit": post_data.get("subreddit"),
                "selftext": post_data.get("selftext", "")[:600],
                "score": post_data.get("score"),
                "num_comments": post_data.get("num_comments"),
                "url": clean_url,
                "top_comments": top_comments[:4]
            }

    # Fallback to DOM extraction
    js_dom = """(function(){
        var post = document.querySelector('shreddit-post');
        var comments = [];
        document.querySelectorAll('shreddit-comment').forEach(function(c){
            var bodyEl = c.querySelector('[slot="comment"]') || c;
            comments.push({
                author: c.getAttribute('author'),
                score: c.getAttribute('score'),
                body: bodyEl.innerText.trim().slice(0, 300)
            });
        });
        return {
            title: post ? post.getAttribute('post-title') : document.title,
            author: post ? post.getAttribute('author') : null,
            score: post ? post.getAttribute('score') : null,
            num_comments: post ? post.getAttribute('comment-count') : null,
            url: window.location.href,
            top_comments: comments.slice(0, 4)
        };
    })()"""
    dom_out = csi_eval_unwrap_func(js_dom, session)
    return dom_out if isinstance(dom_out, dict) else {"url": post_url, "title": "Unknown"}
