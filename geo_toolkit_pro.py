#!/usr/bin/env python3
"""
GEO Toolkit Pro - Generative Engine Optimization desktop app
------------------------------------------------------------
Run:  python geo_toolkit_pro.py       (Python 3.8+, no pip install needed)

Tabs
  1. Analyzer      readability + SEO + credibility + AI-readiness scores, charts, tips
  2. Fact Check    extract DOIs/links, verify via CrossRef and live HTTP check
  3. AI Assistant  rewrite / summarize / FAQ / ideas + AI-visibility test
                   (OpenAI or Perplexity API key needed for this tab only)
  4. Toolkit       track the tools you use, see gaps per category
  5. History       progress chart, batch-analyze a folder, export CSV

Data is stored in ~/.geo_toolkit/   (API keys are NEVER saved to disk;
you can set OPENAI_API_KEY / PERPLEXITY_API_KEY as environment variables).
"""
import csv
import json
import os
import re
import threading
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import Counter
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_DIR = Path.home() / ".geo_toolkit"
APP_DIR.mkdir(exist_ok=True)
HISTORY_FILE = APP_DIR / "history.json"
TOOLKIT_FILE = APP_DIR / "toolkit.json"

# ----------------------------------------------------------------------------
# 1. ANALYSIS ENGINE (pure functions, no GUI)
# ----------------------------------------------------------------------------
STOPWORDS = set("""a an and are as at be but by for from has have he her his i if in into is it its
of on or our she that the their them they this to was we were what when which who will with you your
not can more also than then there these those how why do does did been being about over under such""".split())


def clean_text(raw):
    """Strip HTML tags if the input looks like HTML."""
    if re.search(r"<\w+[^>]*>", raw) and "</" in raw:
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        raw = re.sub(r"(?i)</(p|div|h\d|li|tr)>|<br\s*/?>", "\n", raw)
        raw = re.sub(r"<[^>]+>", " ", raw)
    return raw


def syllables(word):
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith("le") and n > 1:
        n -= 1
    return max(1, n)


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.split()) >= 2]


def get_words(text):
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'’%-]*", text)


def run_checks(items):
    """items: (condition, weight, good_msg, bad_msg) -> (score 0-100, [(ok, msg)])"""
    total = sum(i[1] for i in items) or 1
    got = sum(i[1] for i in items if i[0])
    return round(100 * got / total), [(bool(c), g if c else b) for c, _, g, b in items]


def extract_headings(raw):
    md = re.findall(r"(?m)^#{1,6}\s+(.+)$", raw)
    ht = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"(?is)<h[1-6][^>]*>(.*?)</h[1-6]>", raw)]
    return [h for h in md + ht if h]


def extract_sources(raw):
    urls = [u.rstrip(".,;") for u in re.findall(r"https?://[^\s)\]>\"']+", raw)]
    dois = [d.rstrip(".,;") for d in re.findall(r"\b10\.\d{4,9}/[^\s\"<>,;)]+", raw)]
    return list(dict.fromkeys(dois)), list(dict.fromkeys(urls))


def analyze(raw, keyword="", title="", meta=""):
    text = clean_text(raw)
    words = get_words(text)
    n_words = len(words)
    sents = split_sentences(text)
    n_sent = max(len(sents), 1)
    kw = keyword.strip().lower()
    headings = extract_headings(raw)
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if len(p.split()) >= 3]
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    dois, urls = extract_sources(raw)

    # --- Readability (Hemingway-style) ---
    syl = sum(syllables(w) for w in words) or 1
    flesch = 206.835 - 1.015 * (n_words / n_sent) - 84.6 * (syl / max(n_words, 1))
    avg_len = n_words / n_sent
    long_pct = 100 * sum(1 for s in sents if len(s.split()) > 25) / n_sent
    passive = len(re.findall(r"(?i)\b(?:is|are|was|were|been|being|be)\s+\w+ed\b", text))
    passive_pct = 100 * passive / n_sent
    adverbs = len([w for w in words if w.lower().endswith("ly") and len(w) > 4])
    adv_pct = 100 * adverbs / max(n_words, 1)
    read_score, read_checks = run_checks([
        (50 <= flesch <= 90, 3, f"Reading ease {flesch:.0f} (good, easy to read)",
         f"Reading ease {flesch:.0f} - aim for 50-90 (shorter words and sentences)"),
        (avg_len <= 20, 2, f"Average sentence {avg_len:.0f} words", f"Average sentence {avg_len:.0f} words - keep under 20"),
        (long_pct <= 10, 2, "Very few very long sentences", f"{long_pct:.0f}% sentences exceed 25 words - split them"),
        (passive_pct <= 15, 1, "Mostly active voice", f"Passive voice in ~{passive_pct:.0f}% of sentences - use active voice"),
        (adv_pct <= 2.5, 1, "Few adverbs", f"Adverbs are {adv_pct:.1f}% of words - cut the -ly words"),
    ])

    # --- SEO ---
    first100 = " ".join(w.lower() for w in words[:100])
    count_kw = len(re.findall(re.escape(kw), text.lower())) if kw else 0
    density = 100 * count_kw * max(len(kw.split()), 1) / max(n_words, 1) if kw else 0
    seo_items = [
        (30 <= len(title) <= 60, 2, f"Title length {len(title)} chars (ideal)", f"Title length {len(title)} - ideal is 30-60 characters"),
        (120 <= len(meta) <= 160, 2, f"Meta description {len(meta)} chars (ideal)", f"Meta description {len(meta)} - ideal is 120-160 characters"),
        (n_words >= 300, 2, f"{n_words} words (enough depth)", f"Only {n_words} words - aim for 300+ (600+ for guides)"),
        (len(headings) >= 2, 2, f"{len(headings)} headings found", "Add at least 2 headings (H2/H3) to structure the page"),
        (len(urls) >= 1, 1, f"{len(urls)} link(s) found", "Add internal/external links"),
    ]
    if kw:
        seo_items += [
            (kw in title.lower(), 3, "Keyword is in the title", "Put the keyword in the title"),
            (kw in meta.lower(), 1, "Keyword is in the meta description", "Put the keyword in the meta description"),
            (kw in first100, 3, "Keyword appears in the first 100 words", "Use the keyword in the first 100 words"),
            (any(kw in h.lower() for h in headings), 2, "Keyword appears in a heading", "Use the keyword in at least one heading"),
            (0.5 <= density <= 2.5, 2, f"Keyword density {density:.1f}% (ideal)", f"Keyword density {density:.1f}% - aim for 0.5-2.5%"),
        ]
    seo_score, seo_checks = run_checks(seo_items)
    if not kw:
        seo_checks.append((False, "Enter a target keyword for the full SEO check"))

    # --- Credibility (authority signals) ---
    stats = re.findall(r"\b\d+(?:\.\d+)?\s?%|\b\d{1,3}(?:,\d{3})+\b", text)
    attrib = re.findall(r"(?i)\baccording to\b|\bstudy\b|\bstudies\b|\bresearchers?\b|\bsurvey\b|\breport\b", text)
    years = re.findall(r"\b20[12]\d\b", text)
    cred_score, cred_checks = run_checks([
        (len(urls) + len(dois) >= 2, 3, f"{len(urls) + len(dois)} cited sources", "Cite at least 2 sources (links or DOIs)"),
        (len(dois) >= 1, 2, f"{len(dois)} DOI(s) found", "Add a DOI / academic reference if relevant"),
        (len(stats) >= 3, 2, f"{len(stats)} statistics/numbers", "Add concrete numbers or statistics (3+)"),
        (len(attrib) >= 2, 2, "Attribution phrases present (study, according to...)", "Attribute claims: 'according to ...', 'a study by ...'"),
        (len(years) >= 1, 1, "Dates/years mentioned (freshness)", "Mention dates/years to show the content is current"),
    ])

    # --- AI-readiness (how easily an AI can quote you) ---
    first_para = paragraphs[0] if paragraphs else ""
    fp_words = len(first_para.split())
    questions = [l for l in lines if l.rstrip("# ").endswith("?") and len(l) < 120]
    list_lines = [l for l in lines if re.match(r"^([-*•]|\d+[.)])\s+", l)]
    avg_para = sum(len(p.split()) for p in paragraphs) / max(len(paragraphs), 1)
    definitions = re.findall(r"(?i)\b(?:is a|is an|is the|refers to|means|is defined as)\b", text)
    ai_items = [
        (15 <= fp_words <= 70, 3, "Opens with a short, direct answer paragraph", f"First paragraph is {fp_words} words - open with a 15-70 word direct answer"),
        (len(headings) >= 3, 2, "Clear heading structure", "Use 3+ headings so AI can chunk your content"),
        (len(questions) >= 2, 2, f"{len(questions)} question-style lines (FAQ)", "Add FAQ-style questions people actually ask"),
        (len(list_lines) >= 3, 2, "Lists/bullets present", "Add bulleted or numbered lists (easy to quote)"),
        (avg_para <= 90, 1, f"Short paragraphs (avg {avg_para:.0f} words)", f"Paragraphs average {avg_para:.0f} words - keep under 90"),
        (len(definitions) >= 1, 1, "Contains clear definitions", "Add a definition sentence ('X is a ...')"),
        (len(stats) >= 3, 1, "Quotable facts and numbers", "Add quotable stats - AI loves citing numbers"),
        (n_words >= 600, 1, "Enough depth for AI answers", "Longer, thorough content (600+ words) is cited more"),
    ]
    if kw:
        ai_items.append((kw in first_para.lower(), 2, "Topic keyword is in the opening answer", "Mention the topic keyword in the opening paragraph"))
    ai_score, ai_checks = run_checks(ai_items)

    # --- Top keywords ---
    freq = Counter(w.lower() for w in words if len(w) > 3 and w.lower() not in STOPWORDS and not w.isdigit())
    overall = round((read_score + seo_score + cred_score + ai_score) / 4)
    return {
        "overall": overall, "words": n_words,
        "scores": {"Readability": read_score, "SEO": seo_score, "Credibility": cred_score, "AI-Ready": ai_score},
        "sections": {"Readability": read_checks, "SEO": seo_checks, "Credibility": cred_checks, "AI-Readiness": ai_checks},
        "keywords": freq.most_common(8),
    }


def report_html(res, name):
    rows = ""
    for sec, checks in res["sections"].items():
        rows += f"<h3>{sec}</h3><ul>" + "".join(
            f"<li style='color:{'#067647' if ok else '#b42318'}'>{'✔' if ok else '✘'} {msg}</li>" for ok, msg in checks) + "</ul>"
    bars = "".join(
        f"<div style='margin:6px 0'>{k} <b>{v}</b><div style='background:#eee;height:14px'><div style='width:{v}%;height:14px;"
        f"background:{'#12b76a' if v >= 75 else '#f79009' if v >= 50 else '#f04438'}'></div></div></div>" for k, v in res["scores"].items())
    return (f"<html><head><meta charset='utf-8'><title>GEO Report</title></head><body style='font-family:Segoe UI,Arial;max-width:760px;margin:30px auto'>"
            f"<h1>GEO Report: {name}</h1><p>{datetime.now():%Y-%m-%d %H:%M} &middot; {res['words']} words</p>"
            f"<h2>Overall score: {res['overall']}/100</h2>{bars}{rows}</body></html>")


# ----------------------------------------------------------------------------
# 2. NETWORK HELPERS
# ----------------------------------------------------------------------------
UA = {"User-Agent": "GEOToolkitPro/1.0"}


def check_doi(doi):
    req = urllib.request.Request("https://api.crossref.org/works/" + urllib.parse.quote(doi), headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        title = (data["message"].get("title") or ["(no title)"])[0]
        return True, "Found: " + title[:90]
    except urllib.error.HTTPError as e:
        return False, "Not found in CrossRef" if e.code == 404 else f"HTTP {e.code}"
    except Exception as e:
        return False, f"Error: {e}"


def check_url(url):
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, headers=UA, method=method)
            with urllib.request.urlopen(req, timeout=15) as r:
                return True, f"OK ({r.status})"
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405):
                continue
            return False, f"HTTP {e.code}"
        except Exception as e:
            return False, f"Error: {str(e)[:60]}"
    return False, "Unreachable"


PROVIDERS = {
    "OpenAI (ChatGPT)": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini", "OPENAI_API_KEY"),
    "Perplexity": ("https://api.perplexity.ai/chat/completions", "sonar", "PERPLEXITY_API_KEY"),
}


def ai_call(provider, key, model, prompt):
    url, _, _ = PROVIDERS[provider]
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}", **UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}")
    return data["choices"][0]["message"]["content"], data.get("citations", [])


# ----------------------------------------------------------------------------
# 3. SIMPLE CHART WIDGET (pure Tk canvas, no matplotlib needed)
# ----------------------------------------------------------------------------
def score_color(v):
    return "#12b76a" if v >= 75 else "#f79009" if v >= 50 else "#f04438"


class Chart(tk.Canvas):
    def __init__(self, master, height=230):
        super().__init__(master, height=height, bg="white", highlightthickness=1, highlightbackground="#d0d5dd")
        self._fn = None
        self.bind("<Configure>", lambda e: self.redraw())

    def redraw(self):
        self.delete("all")
        if self._fn:
            self._fn()
        else:
            self.create_text(self.winfo_width() / 2, self.winfo_height() / 2, text="No data yet", fill="#98a2b3")

    def _grid(self, w, h, l, r, t, b, title):
        self.create_text(10, 12, text=title, anchor="w", font=("Segoe UI", 10, "bold"), fill="#101828")
        for g in (0, 25, 50, 75, 100):
            y = t + (h - t - b) * (1 - g / 100)
            self.create_line(l, y, w - r, y, fill="#eaecf0")
            self.create_text(l - 5, y, text=str(g), anchor="e", fill="#667085", font=("Segoe UI", 8))

    def bars(self, labels, values, title=""):
        def draw():
            w, h = max(self.winfo_width(), 200), max(self.winfo_height(), 120)
            l, r, t, b = 36, 10, 28, 30
            self._grid(w, h, l, r, t, b, title)
            slot = (w - l - r) / max(len(values), 1)
            for i, (lab, v) in enumerate(zip(labels, values)):
                x0, x1 = l + i * slot + slot * 0.2, l + (i + 1) * slot - slot * 0.2
                y1 = h - b
                y0 = y1 - (h - t - b) * min(v, 100) / 100
                self.create_rectangle(x0, y0, x1, y1, fill=score_color(v), outline="")
                self.create_text((x0 + x1) / 2, y0 - 8, text=str(round(v)), font=("Segoe UI", 9, "bold"))
                self.create_text((x0 + x1) / 2, y1 + 12, text=lab, font=("Segoe UI", 8), fill="#475467")
        self._fn = draw
        self.redraw()

    def lines(self, xlabels, series, title=""):
        palette = ["#1570ef", "#12b76a", "#f79009", "#7a5af8", "#f04438"]

        def draw():
            w, h = max(self.winfo_width(), 200), max(self.winfo_height(), 120)
            l, r, t, b = 36, 10, 28, 30
            self._grid(w, h, l, r, t, b, title)
            n = len(xlabels)
            step = (w - l - r) / max(n - 1, 1)
            xs = [l + (w - l - r) / 2 if n == 1 else l + i * step for i in range(n)]
            for ci, (name, vals) in enumerate(series.items()):
                col = palette[ci % len(palette)]
                pts = [(xs[i], t + (h - t - b) * (1 - v / 100)) for i, v in enumerate(vals)]
                if len(pts) > 1:
                    self.create_line(*[c for p in pts for c in p], fill=col, width=2)
                for x, y in pts:
                    self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=col, outline="")
                self.create_text(w - r - 4 - 85 * (len(series) - 1 - ci), 12, text="● " + name, fill=col, anchor="e", font=("Segoe UI", 8))
            for i in range(0, n, max(1, n // 8)):
                self.create_text(xs[i], h - b + 12, text=xlabels[i], font=("Segoe UI", 8), fill="#475467")
        self._fn = draw
        self.redraw()


# ----------------------------------------------------------------------------
# 4. TOOLKIT DEFAULTS
# ----------------------------------------------------------------------------
G = "https://www.google.com/search?q="
DEFAULT_TOOLS = [
    ("AI Writing", "WriteSonic", "Using", "https://writesonic.com"),
    ("AI Writing", "ChatGPT", "Using", "https://chatgpt.com"),
    ("AI Writing", "Jasper", "Not using", "https://www.jasper.ai"),
    ("SEO Analysis", "SEMrush", "Using", "https://www.semrush.com"),
    ("SEO Analysis", "Ahrefs", "Not using", "https://ahrefs.com"),
    ("SEO Analysis", "SEO Meta", "Planned", G + "SEO+META+in+1+CLICK"),
    ("Fact-Checking", "Google Scholar", "Planned", "https://scholar.google.com"),
    ("Fact-Checking", "CrossRef", "Planned", "https://www.crossref.org"),
    ("Fact-Checking", "Credibility Checker", "Not using", G + "credibility+checker"),
    ("AI Testing", "Perplexity", "Planned", "https://www.perplexity.ai"),
    ("AI Testing", "ChatGPT", "Using", "https://chatgpt.com"),
    ("AI Testing", "Google AI Overviews", "Planned", "https://www.google.com"),
    ("Visual & Readability", "Grammarly", "Not using", "https://www.grammarly.com"),
    ("Visual & Readability", "Hemingway App", "Not using", "https://hemingwayapp.com"),
    ("Visual & Readability", "Canva", "Not using", "https://www.canva.com"),
    ("Visual & Readability", "Piktochart", "Not using", "https://piktochart.com"),
    ("Organizing", "Google Docs", "Using", "https://docs.google.com"),
]
CATEGORIES = ["AI Writing", "SEO Analysis", "Fact-Checking", "AI Testing", "Visual & Readability", "Organizing"]


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------
# 5. GUI
# ----------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GEO Toolkit Pro")
        self.geometry("1180x760")
        self.minsize(980, 640)
        ttk.Style(self).theme_use("clam")
        ttk.Style(self).configure("TNotebook.Tab", padding=(14, 6), font=("Segoe UI", 10))
        self.history = load_json(HISTORY_FILE, [])
        self.tools = load_json(TOOLKIT_FILE, None) or [dict(cat=c, tool=t, status=s, url=u) for c, t, s, u in DEFAULT_TOOLS]
        self.last_result = None
        self._after_id = None

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.nb = nb
        self.tab_an, self.tab_fc, self.tab_ai, self.tab_tk, self.tab_hi = (ttk.Frame(nb) for _ in range(5))
        for tab, label in ((self.tab_an, "1  Analyzer"), (self.tab_fc, "2  Fact Check"), (self.tab_ai, "3  AI Assistant"),
                           (self.tab_tk, "4  Toolkit"), (self.tab_hi, "5  History")):
            nb.add(tab, text=label)
        self.build_analyzer()
        self.build_factcheck()
        self.build_ai()
        self.build_toolkit()
        self.build_history()

    # ---------- helpers ----------
    def bg(self, fn, done):
        def run():
            try:
                res, err = fn(), None
            except Exception as e:  # noqa
                res, err = None, e
            self.after(0, lambda: done(res, err))
        threading.Thread(target=run, daemon=True).start()

    def article(self):
        return self.txt.get("1.0", "end").strip()

    # ---------- TAB 1: ANALYZER ----------
    def build_analyzer(self):
        t = self.tab_an
        t.columnconfigure(0, weight=1)
        t.columnconfigure(1, weight=1)
        t.rowconfigure(0, weight=1)
        left, right = ttk.Frame(t), ttk.Frame(t)
        left.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        right.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)

        form = ttk.Frame(left)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.kw_var, self.title_var, self.meta_var, self.name_var = (tk.StringVar() for _ in range(4))
        for i, (lab, var) in enumerate((("Target keyword", self.kw_var), ("Page title", self.title_var),
                                        ("Meta description", self.meta_var), ("Content name", self.name_var))):
            ttk.Label(form, text=lab).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(form, textvariable=var)
            e.grid(row=i, column=1, sticky="ew", padx=6, pady=2)
            e.bind("<KeyRelease>", lambda ev: self.schedule())
        ttk.Label(left, text="Paste your article / HTML here (or load a file):").pack(anchor="w", pady=(8, 2))
        box = ttk.Frame(left)
        box.pack(fill="both", expand=True)
        self.txt = tk.Text(box, wrap="word", font=("Segoe UI", 10), undo=True)
        sb = ttk.Scrollbar(box, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.bind("<KeyRelease>", lambda ev: self.schedule())

        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="Load file", command=self.load_file).pack(side="left")
        ttk.Button(bar, text="Analyze now", command=self.run_analysis).pack(side="left", padx=4)
        ttk.Button(bar, text="Save to history", command=self.save_history).pack(side="left")
        ttk.Button(bar, text="Export report", command=self.export_report).pack(side="left", padx=4)
        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Auto-analyze while typing", variable=self.auto_var).pack(side="right")

        self.big = tk.Label(right, text="Overall: --", font=("Segoe UI", 20, "bold"), anchor="w")
        self.big.pack(fill="x")
        self.chart = Chart(right, 210)
        self.chart.pack(fill="x", pady=4)
        self.kwchart = Chart(right, 150)
        self.kwchart.pack(fill="x", pady=4)
        dbox = ttk.Frame(right)
        dbox.pack(fill="both", expand=True)
        self.details = tk.Text(dbox, wrap="word", font=("Segoe UI", 10), state="disabled", bg="#f9fafb")
        sb2 = ttk.Scrollbar(dbox, command=self.details.yview)
        self.details.configure(yscrollcommand=sb2.set)
        sb2.pack(side="right", fill="y")
        self.details.pack(side="left", fill="both", expand=True)
        self.details.tag_configure("ok", foreground="#067647")
        self.details.tag_configure("bad", foreground="#b42318")
        self.details.tag_configure("head", font=("Segoe UI", 11, "bold"), spacing1=8)

    def schedule(self):
        if self.auto_var.get():
            if self._after_id:
                self.after_cancel(self._after_id)
            self._after_id = self.after(900, self.run_analysis)

    def load_file(self):
        p = filedialog.askopenfilename(filetypes=[("Text/HTML/Markdown", "*.txt *.md *.html *.htm"), ("All", "*.*")])
        if p:
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", Path(p).read_text(encoding="utf-8", errors="ignore"))
            self.name_var.set(Path(p).stem)
            self.run_analysis()

    def run_analysis(self):
        raw = self.article()
        if not raw:
            return
        res = analyze(raw, self.kw_var.get(), self.title_var.get(), self.meta_var.get())
        self.last_result = res
        self.big.configure(text=f"Overall: {res['overall']}/100    ({res['words']} words)", fg=score_color(res["overall"]))
        self.chart.bars(list(res["scores"]), list(res["scores"].values()), "Scores (0-100)")
        if res["keywords"]:
            mx = res["keywords"][0][1]
            self.kwchart.bars([k for k, _ in res["keywords"]], [100 * c / mx for _, c in res["keywords"]], "Top words (relative frequency)")
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        for sec, checks in res["sections"].items():
            self.details.insert("end", sec + "\n", "head")
            for ok, msg in checks:
                self.details.insert("end", ("  ✔ " if ok else "  ✘ ") + msg + "\n", "ok" if ok else "bad")
        self.details.configure(state="disabled")

    def record(self, name, kw, res):
        return {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "name": name or "Untitled", "keyword": kw,
                "overall": res["overall"], "words": res["words"], **{k: v for k, v in res["scores"].items()}}

    def save_history(self):
        if not self.last_result:
            self.run_analysis()
        if not self.last_result:
            return messagebox.showinfo("Nothing to save", "Paste some content first.")
        self.history.append(self.record(self.name_var.get(), self.kw_var.get(), self.last_result))
        save_json(HISTORY_FILE, self.history)
        self.refresh_history()
        messagebox.showinfo("Saved", "Saved to History tab.")

    def export_report(self):
        if not self.last_result:
            self.run_analysis()
        if not self.last_result:
            return
        p = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML", "*.html")], initialfile="geo_report.html")
        if p:
            Path(p).write_text(report_html(self.last_result, self.name_var.get() or "Content"), encoding="utf-8")
            webbrowser.open(Path(p).as_uri())

    # ---------- TAB 2: FACT CHECK ----------
    def build_factcheck(self):
        t = self.tab_fc
        top = ttk.Frame(t)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Button(top, text="Extract sources from article", command=self.fc_extract).pack(side="left")
        ttk.Button(top, text="Verify all online", command=self.fc_verify).pack(side="left", padx=6)
        ttk.Button(top, text="Open selected", command=self.fc_open).pack(side="left")
        ttk.Label(top, text="DOIs are checked with CrossRef, links with a live request. Double-click a row to open it.").pack(side="left", padx=10)
        cols = ("type", "source", "status")
        self.fc = ttk.Treeview(t, columns=cols, show="headings")
        for c, w in (("type", 70), ("source", 560), ("status", 380)):
            self.fc.heading(c, text=c.title())
            self.fc.column(c, width=w)
        self.fc.pack(fill="both", expand=True, padx=8, pady=4)
        self.fc.tag_configure("good", foreground="#067647")
        self.fc.tag_configure("bad", foreground="#b42318")
        self.fc.bind("<Double-1>", lambda e: self.fc_open())
        self.fc_summary = ttk.Label(t, text="")
        self.fc_summary.pack(anchor="w", padx=8, pady=6)
        tips = ("Tip: also look up your key claims on Google Scholar (select a row, then press the button).")
        ttk.Label(t, text=tips, foreground="#667085").pack(anchor="w", padx=8)
        ttk.Button(t, text="Search selected on Google Scholar", command=self.fc_scholar).pack(anchor="w", padx=8, pady=6)

    def fc_extract(self):
        self.fc.delete(*self.fc.get_children())
        dois, urls = extract_sources(self.article())
        for d in dois:
            self.fc.insert("", "end", values=("DOI", d, "not checked"))
        for u in urls:
            self.fc.insert("", "end", values=("Link", u, "not checked"))
        self.fc_summary.configure(text=f"{len(dois)} DOI(s) and {len(urls)} link(s) found." if dois or urls else
                                  "No sources found in your article - add citations to boost credibility.")

    def fc_verify(self):
        items = [(i, *self.fc.item(i, "values")[:2]) for i in self.fc.get_children()]
        if not items:
            self.fc_extract()
            items = [(i, *self.fc.item(i, "values")[:2]) for i in self.fc.get_children()]

        def work():
            out = []
            for iid, typ, src in items:
                ok, msg = check_doi(src) if typ == "DOI" else check_url(src)
                out.append((iid, ok, msg))
                self.after(0, lambda i=iid, o=ok, m=msg, ty=typ, s=src: self.fc.item(i, values=(ty, s, ("✔ " if o else "✘ ") + m), tags=("good" if o else "bad",)))
            return out

        def done(res, err):
            if err:
                return messagebox.showerror("Error", str(err))
            good = sum(1 for _, ok, _ in res if ok)
            self.fc_summary.configure(text=f"Verified: {good}/{len(res)} sources are valid.")
        self.fc_summary.configure(text="Checking... please wait")
        self.bg(work, done)

    def fc_open(self):
        sel = self.fc.selection()
        if sel:
            typ, src = self.fc.item(sel[0], "values")[:2]
            webbrowser.open("https://doi.org/" + src if typ == "DOI" else src)

    def fc_scholar(self):
        q = simpledialog.askstring("Google Scholar", "Claim or topic to search:", initialvalue=self.kw_var.get())
        if q:
            webbrowser.open("https://scholar.google.com/scholar?q=" + urllib.parse.quote(q))

    # ---------- TAB 3: AI ASSISTANT ----------
    def build_ai(self):
        t = self.tab_ai
        top = ttk.Frame(t)
        top.pack(fill="x", padx=8, pady=8)
        self.prov = tk.StringVar(value="OpenAI (ChatGPT)")
        self.model = tk.StringVar(value=PROVIDERS[self.prov.get()][1])
        self.key = tk.StringVar(value=os.environ.get(PROVIDERS[self.prov.get()][2], ""))
        ttk.Label(top, text="Provider").grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(top, textvariable=self.prov, values=list(PROVIDERS), state="readonly", width=20)
        cb.grid(row=0, column=1, padx=6)
        cb.bind("<<ComboboxSelected>>", self.prov_changed)
        ttk.Label(top, text="Model").grid(row=0, column=2, padx=(10, 0))
        ttk.Entry(top, textvariable=self.model, width=18).grid(row=0, column=3, padx=6)
        ttk.Label(top, text="API key").grid(row=0, column=4, padx=(10, 0))
        ttk.Entry(top, textvariable=self.key, show="*", width=34).grid(row=0, column=5, padx=6)
        ttk.Label(top, text="(key stays in memory only)", foreground="#667085").grid(row=0, column=6)

        mid = ttk.Frame(t)
        mid.pack(fill="x", padx=8, pady=4)
        mid.columnconfigure(1, weight=1)
        self.mode = tk.StringVar(value="Visibility test (ask AI a question)")
        self.modes = ["Visibility test (ask AI a question)", "Rewrite my article for clarity", "Summarize (answer-first)",
                      "Generate FAQ from my article", "Content ideas for keyword/topic"]
        ttk.Label(mid, text="Task").grid(row=0, column=0, sticky="w")
        ttk.Combobox(mid, textvariable=self.mode, values=self.modes, state="readonly", width=38).grid(row=0, column=1, sticky="w", padx=6, pady=2)
        self.q_var, self.brand_var = tk.StringVar(), tk.StringVar()
        ttk.Label(mid, text="Question / topic").grid(row=1, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.q_var).grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        ttk.Label(mid, text="Brand / domain(s)").grid(row=2, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.brand_var).grid(row=2, column=1, sticky="ew", padx=6, pady=2)
        ttk.Label(mid, text="comma separated, e.g. MyBrand, mybrand.com", foreground="#667085").grid(row=2, column=2, sticky="w")
        self.run_btn = ttk.Button(mid, text="Run", command=self.ai_run)
        self.run_btn.grid(row=3, column=1, sticky="w", padx=6, pady=6)

        box = ttk.Frame(t)
        box.pack(fill="both", expand=True, padx=8, pady=6)
        self.ai_out = tk.Text(box, wrap="word", font=("Segoe UI", 10), state="disabled", bg="#f9fafb")
        sb = ttk.Scrollbar(box, command=self.ai_out.yview)
        self.ai_out.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.ai_out.pack(side="left", fill="both", expand=True)

    def prov_changed(self, e):
        self.model.set(PROVIDERS[self.prov.get()][1])
        self.key.set(os.environ.get(PROVIDERS[self.prov.get()][2], ""))

    def ai_run(self):
        k = self.key.get().strip()
        if not k:
            return messagebox.showerror("API Key Missing", "Please enter an API key for the selected provider.")
        m = self.mode.get()
        q = self.q_var.get().strip()
        art = self.article()
        prompt = ""
        if "Visibility" in m:
            if not q:
                return messagebox.showerror("Missing input", "Enter a question to test AI visibility.")
            brands = [b.strip() for b in self.brand_var.get().split(",") if b.strip()]
            prompt = f"Answer this user question thoroughly and objectively: '{q}'\n\nIf relevant, mention standard industry sources or brands."
        elif "Rewrite" in m:
            prompt = f"Rewrite the following text for maximum clarity, readability, and GEO (Generative Engine Optimization) readiness (short paragraphs, direct answers, bullet points):\n\n{art}"
        elif "Summarize" in m:
            prompt = f"Summarize the following text using an answer-first format (lead with the core takeaway, followed by 3-4 bullet points):\n\n{art}"
        elif "FAQ" in m:
            prompt = f"Generate 4-5 frequently asked questions (FAQs) with concise direct answers based on this text:\n\n{art}"
        elif "Content ideas" in m:
            if not q:
                q = self.kw_var.get() or "this topic"
            prompt = f"Suggest 5 content outline ideas (with H2 headings and key sub-points) optimized for Generative Engine Optimization (GEO) about: {q}"

        def work():
            text, cits = ai_call(self.prov.get(), k, self.model.get(), prompt)
            return text, cits

        def done(res, err):
            self.run_btn.configure(state="normal")
            if err:
                return messagebox.showerror("AI Error", str(err))
            text, cits = res
            self.ai_out.configure(state="normal")
            self.ai_out.delete("1.0", "end")
            self.ai_out.insert("end", text + "\n")
            if cits:
                self.ai_out.insert("end", "\n\nCitations / Sources returned by AI:\n" + "\n".join(f"- {c}" for c in cits))
            if "Visibility" in m and self.brand_var.get():
                brands = [b.strip().lower() for b in self.brand_var.get().split(",") if b.strip()]
                found = [b for b in brands if b in text.lower()]
                self.ai_out.insert("end", f"\n\nBrand Visibility Check: {'FOUND (' + ', '.join(found) + ')' if found else 'NOT FOUND in AI response.'}")
            self.ai_out.configure(state="disabled")

        self.run_btn.configure(state="disabled")
        self.ai_out.configure(state="normal")
        self.ai_out.delete("1.0", "end")
        self.ai_out.insert("end", "Running AI request, please wait...")
        self.ai_out.configure(state="disabled")
        self.bg(work, done)

    # ---------- TAB 4: TOOLKIT ----------
    def build_toolkit(self):
        t = self.tab_tk
        top = ttk.Frame(t)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Label(top, text="Track your stack across the 5 GEO categories. Click 'Open' to visit tools.", font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(top, text="Open selected tool", command=self.tk_open).pack(side="right")
        ttk.Button(top, text="Cycle status (Using -> Not -> Planned)", command=self.tk_cycle).pack(side="right", padx=6)

        cols = ("category", "tool", "status", "url")
        self.tree = ttk.Treeview(t, columns=cols, show="headings")
        for c, w in (("category", 170), ("tool", 210), ("status", 110), ("url", 640)):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree.bind("<Double-1>", lambda e: self.tk_open())
        self.refresh_toolkit()

    def refresh_toolkit(self):
        self.tree.delete(*self.tree.get_children())
        for row in self.tools:
            self.tree.insert("", "end", values=(row["cat"], row["tool"], row["status"], row["url"]))
        save_json(TOOLKIT_FILE, self.tools)

    def tk_cycle(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        st = ["Using", "Not using", "Planned"]
        cur = self.tools[idx]["status"]
        self.tools[idx]["status"] = st[(st.index(cur) + 1) % len(st)] if cur in st else "Using"
        self.refresh_toolkit()
        self.tree.selection_set(self.tree.get_children()[idx])

    def tk_open(self):
        sel = self.tree.selection()
        if sel:
            url = self.tree.item(sel[0], "values")[3]
            if url:
                webbrowser.open(url)

    # ---------- TAB 5: HISTORY ----------
    def build_history(self):
        t = self.tab_hi
        top = ttk.Frame(t)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Button(top, text="Delete selected", command=self.hi_del).pack(side="left")
        ttk.Button(top, text="Clear all history", command=self.hi_clear).pack(side="left", padx=6)
        ttk.Button(top, text="Batch analyze folder...", command=self.hi_batch).pack(side="left", padx=6)
        ttk.Button(top, text="Export CSV", command=self.hi_csv).pack(side="right")

        self.hchart = Chart(t, 200)
        self.hchart.pack(fill="x", padx=8, pady=4)

        cols = ("time", "name", "keyword", "overall", "words", "Readability", "SEO", "Credibility", "AI-Ready")
        self.htree = ttk.Treeview(t, columns=cols, show="headings")
        for c, w in zip(cols, (120, 190, 140, 75, 65, 90, 60, 90, 80)):
            self.htree.heading(c, text=c)
            self.htree.column(c, width=w)
        self.htree.pack(fill="both", expand=True, padx=8, pady=4)
        self.refresh_history()

    def refresh_history(self):
        self.htree.delete(*self.htree.get_children())
        for row in self.history:
            self.htree.insert("", "end", values=(row.get("time", ""), row.get("name", ""), row.get("keyword", ""),
                                                 row.get("overall", ""), row.get("words", ""),
                                                 row.get("Readability", ""), row.get("SEO", ""),
                                                 row.get("Credibility", ""), row.get("AI-Ready", "")))
        if self.history:
            labs = [r["name"][:12] + f" ({r['time'][5:10]})" for r in self.history]
            self.hchart.lines(labs, {"Overall score": [r["overall"] for r in self.history]}, "Progress history")
        else:
            self.hchart.redraw()

    def hi_del(self):
        for sel in self.htree.selection():
            idx = self.htree.index(sel)
            del self.history[idx]
        save_json(HISTORY_FILE, self.history)
        self.refresh_history()

    def hi_clear(self):
        if messagebox.askyesno("Clear history", "Are you sure?"):
            self.history = []
            save_json(HISTORY_FILE, self.history)
            self.refresh_history()

    def hi_batch(self):
        d = filedialog.askdirectory()
        if not d:
            return
        count = 0
        for p in Path(d).glob("*.*"):
            if p.suffix.lower() in (".txt", ".md", ".html", ".htm"):
                try:
                    raw = p.read_text(encoding="utf-8", errors="ignore")
                    res = analyze(raw)
                    self.history.append(self.record(p.stem, "", res))
                    count += 1
                except Exception:
                    pass
        save_json(HISTORY_FILE, self.history)
        self.refresh_history()
        messagebox.showinfo("Batch analyze", f"Successfully analyzed and added {count} file(s).")

    def hi_csv(self):
        if not self.history:
            return messagebox.showinfo("No history", "Nothing to export.")
        p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="geo_history.csv")
        if p:
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(self.history[0].keys()))
                w.writeheader()
                w.writerows(self.history)
            webbrowser.open(Path(p).as_uri())


if __name__ == "__main__":
    App().mainloop()