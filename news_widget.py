#!/usr/bin/env python3
"""
베트남 투자 레이더 — 물류 영업용 데스크톱 위젯
- 뉴스 수집: Google 뉴스 RSS + 지정 RSS (무료)
- 번역: Google 번역 (deep-translator, 무료)
- 분류: 키워드 규칙 (AI/API 크레딧 사용 없음)

사용법
  python news_widget.py --collect          뉴스 수집만 (작업 스케줄러용)
  python news_widget.py --widget           위젯 창 열기 (자동 수집 포함)
  python news_widget.py --install-startup  윈도우 시작 시 자동 실행 등록
  python news_widget.py --uninstall-startup
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import webbrowser
from pathlib import Path
from urllib.parse import quote

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
DATA_PATH = BASE / "news_data.json"
TEMPLATE_PATH = BASE / "widget_template.html"
HTML_PATH = BASE / "widget.html"
LOG_PATH = BASE / "radar.log"
CHECKS_PATH = BASE / "checked.json"
WEB_MODE = False                      # --web: GitHub 클라우드 수집 모드
WEB_DIR = BASE / "web"                # 웹 앱 정적 파일 (manifest, 아이콘, sw.js)
SITE_DIR = BASE / "site"              # GitHub Pages로 배포되는 폴더

socket.setdefaulttimeout(20)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def log(msg):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    if WEB_MODE:
        cfg.update(cfg.get("web", {}))
        cfg["proxy"] = ""                       # 클라우드는 회사 프록시 불필요
        cfg.pop("translator_routes", None)
    env_mail = os.environ.get("MYMEMORY_EMAIL", "").strip()
    if env_mail:
        cfg["mymemory_email"] = env_mail
    return cfg


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


# ─────────────────────────── 분류 규칙 ───────────────────────────
# 영문(ASCII) 키워드는 단어 경계로 매칭, 끝에 *가 있으면 접두어 매칭(inaugurat* → inaugurated)
def _compile(keywords, case_sensitive=False):
    pats = []
    for kw in keywords:
        prefix = kw.endswith("*")
        k = nfc(kw.rstrip("*"))
        flags = 0 if case_sensitive else re.IGNORECASE
        if k.isascii():
            p = r"(?<![A-Za-z])" + re.escape(k) + ("" if prefix else r"(?![A-Za-z])")
        else:
            p = re.escape(k)
        pats.append(re.compile(p, flags))
    return pats


def _any(pats, text):
    return any(p.search(text) for p in pats)


STAGES = [  # 위에서부터 우선 판정
    ("construct", "착공", "설비·건자재 수입, 프로젝트 화물 수요가 생기는 시점", [
        "khởi công", "động thổ", "groundbreaking", "break ground", "breaks ground", "broke ground",
        "construction begins", "begin construction", "begins construction", "starts construction",
        "착공", "기공식"]),
    ("operate", "가동·준공", "원자재 인바운드·완제품 수출 정기 물량 제안 시점", [
        "khánh thành", "chính thức đi vào hoạt động", "chính thức vận hành", "inaugurat*",
        "begins operation*", "began operation*", "starts operation*", "commence* operation*",
        "commence* production", "starts production", "begins production", "준공", "가동 시작",
        "가동에 들어", "양산"]),
    ("license", "인허가", "초기 접촉 적기: 현지 법인·구매 담당자 파악", [
        "giấy chứng nhận đăng ký đầu tư", "giấy chứng nhận đầu tư", "cấp phép", "chấp thuận chủ trương",
        "investment certificate", "investment registration certificate", "investment licen*",
        "approved", "approval", "투자허가", "투자 허가", "인허가", "승인"]),
    ("plan", "계획·MOU", "관심 리스트에 등록하고 진행 상황 추적", [
        "mou", "biên bản ghi nhớ", "ghi nhớ hợp tác", "kế hoạch đầu tư", "dự kiến đầu tư", "đề xuất",
        "plans to", "plan to", "to invest", "eyes", "considering", "양해각서", "추진", "검토", "계획"]),
]
STAGE_RULES = [(k, label, hint, _compile(kws)) for k, label, hint, kws in STAGES]
OTHER_STAGE = ("other", "동향", "시장 동향 참고")

# 물류영업 관점 7개 분야: (키, 이름, 영업 포인트, 키워드)
TOPICS = [
    ("invest", "투자·공장", "공장 신설·증설: 설비 반입부터 정기 물량까지 선점", [
        "nhà máy", "đầu tư", "fdi", "khu công nghiệp", "khởi công", "mở rộng sản xuất",
        "factory", "factories", "plant", "invest*", "industrial park*", "groundbreaking", "expan*",
        "공장", "투자", "착공", "증설", "산업단지"]),
    ("tariff", "관세·통상", "고객사 원가·원산지 영향 → 통관·대체 루트 제안 기회", [
        "thuế quan", "thuế đối ứng", "thuế nhập khẩu", "thuế xuất khẩu", "chống bán phá giá",
        "phòng vệ thương mại", "hiệp định thương mại", "xuất xứ", "evfta", "cptpp", "rcep",
        "tariff*", "anti-dumping", "countervailing", "trade deal", "trade agreement", "trade war",
        "transship*", "rules of origin", "관세", "fta", "반덤핑", "원산지", "통상"]),
    ("trade", "수출입 동향", "품목·국가별 물량 변화 → 성장 품목 화주 공략", [
        "kim ngạch", "xuất khẩu", "nhập khẩu", "xuất nhập khẩu", "thặng dư thương mại", "cán cân thương mại",
        "export*", "import*", "trade surplus", "trade deficit", "shipments", "수출", "수입", "무역수지"]),
    ("logistics", "물류·운송", "운임·항만·인프라 변화 → 서비스·견적 전략 점검", [
        "logistics", "vận tải", "cảng biển", "cảng", "container", "cước", "kho bãi", "đường sắt",
        "hàng không", "sân bay", "chuyển phát", "giao hàng", "freight", "shipping", "seaport", "port",
        "ports", "warehouse*", "trucking", "railway*", "air cargo", "cargo", "3pl", "물류", "운송",
        "해운", "항만", "운임", "창고", "컨테이너", "항공화물", "철도", "택배"]),
    ("law", "법령·제도", "통관·세제 규정 변경 → 고객 안내·컴플라이언스 포인트", [
        "nghị định", "thông tư", "dự thảo luật", "luật", "quy định mới", "hải quan", "thủ tục",
        "decree", "circular", "regulation*", "customs", "new rules", "legislation",
        "법령", "시행령", "규정", "통관", "세관", "제도", "법안", "개정"]),
    ("consumer", "소비·유통", "내수·이커머스 성장 → 유통·라스트마일 물류 수요", [
        "bán lẻ", "tiêu dùng", "sức mua", "thương mại điện tử", "siêu thị", "chuỗi cửa hàng",
        "retail*", "consumer*", "consumption", "e-commerce", "ecommerce", "shopee", "lazada",
        "tiktok shop", "소비", "유통", "이커머스", "리테일", "편의점", "온라인 쇼핑"]),
    ("industry", "산업 동향", "업종별 경기 흐름 → 타깃 업종·고객 우선순위 조정", [
        "sản xuất công nghiệp", "chỉ số pmi", "pmi", "ngành", "đơn hàng", "tăng trưởng", "gdp",
        "manufacturing", "industry", "industrial production", "new orders", "outlook",
        "산업", "제조업", "경기", "성장률"]),
]
TOPIC_RULES = [(k, l, h, _compile(kws)) for k, l, h, kws in TOPICS]
TOPIC_INFO = {k: (l, h) for k, l, h, _ in TOPICS}

SCORE_RULES = [(w, _compile(kws)) for w, kws in [
    (3, ["nhà máy", "factory", "factories", "plant", "공장", "khởi công", "động thổ", "groundbreaking",
         "giấy chứng nhận đăng ký đầu tư", "investment certificate", "투자허가", "착공"]),
    (2, ["fdi", "đầu tư", "investment", "invest*", "투자", "khu công nghiệp", "industrial park*",
         "industrial zone*", "산업단지", "mở rộng", "expan*", "증설", "khánh thành", "inaugurat*", "준공", "가동"]),
    (1, ["logistics", "kho bãi", "warehouse*", "cảng", "seaport", "물류", "xuất khẩu", "export*", "수출",
         "sản xuất", "manufactur*", "production", "생산"]),
]]

INDUSTRIES = [(label, _compile(kws)) for label, kws in [
    ("전자·반도체", ["bán dẫn", "điện tử", "linh kiện", "semiconductor*", "chip", "chips", "electronic*", "반도체", "전자", "부품"]),
    ("섬유·신발", ["dệt may", "may mặc", "da giày", "textile*", "garment*", "apparel", "footwear", "shoe*", "섬유", "의류", "신발", "봉제"]),
    ("배터리·EV", ["xe điện", "pin lithium", "battery", "batteries", "electric vehicle*", "배터리", "전기차"]),
    ("자동차", ["ô tô", "xe máy", "automotive", "automobile*", "carmaker*", "자동차"]),
    ("화학·소재", ["hóa chất", "nhựa", "chemical*", "plastic*", "polymer*", "화학", "소재", "플라스틱"]),
    ("철강·금속", ["thép", "kim loại", "steel", "alumin*", "철강", "금속"]),
    ("식품·농수산", ["thực phẩm", "thủy sản", "food", "seafood", "beverage*", "식품", "수산"]),
    ("에너지", ["điện gió", "điện mặt trời", "năng lượng", "lng", "solar", "wind power", "energy", "에너지", "발전소", "태양광", "풍력"]),
    ("물류·창고", ["logistics", "kho bãi", "kho lạnh", "cảng", "warehouse*", "seaport", "cold storage", "물류", "창고", "항만"]),
    ("산업단지", ["khu công nghiệp", "cụm công nghiệp", "industrial park*", "industrial zone*", "산업단지", "공단"]),
    ("가구·목재", ["đồ gỗ", "nội thất", "furniture", "wood*", "가구", "목재"]),
]]

COUNTRIES = [(label, _compile(kws, case_sensitive=True)) for label, kws in [
    ("한국계", ["Hàn Quốc", "Korea", "Korean", "한국"]),
    ("중국계", ["Trung Quốc", "China", "Chinese", "중국"]),
    ("일본계", ["Nhật Bản", "Japan", "Japanese", "일본"]),
    ("대만계", ["Đài Loan", "Taiwan", "Taiwanese", "대만"]),
    ("싱가포르계", ["Singapore", "싱가포르"]),
    ("미국계", ["Hoa Kỳ", "nhà đầu tư Mỹ", "doanh nghiệp Mỹ", "U.S.", "American", "US-based", "미국"]),
    ("유럽계", ["châu Âu", "Europe", "European", "Đức", "Germany", "German", "유럽", "독일"]),
]]

# (한국어 이름, 권역, 별칭들) — 2025년 성 통합 이전 이름도 함께 인식
REGIONS = [
    ("하노이", "북부", ["Hà Nội", "Hanoi", "Ha Noi", "하노이"]),
    ("하이퐁", "북부", ["Hải Phòng", "Hai Phong", "하이퐁"]),
    ("박닌", "북부", ["Bắc Ninh", "Bac Ninh", "박닌"]),
    ("박장", "북부", ["Bắc Giang", "Bac Giang", "박장"]),
    ("하이즈엉", "북부", ["Hải Dương", "Hai Duong", "하이즈엉"]),
    ("흥옌", "북부", ["Hưng Yên", "Hung Yen", "흥옌"]),
    ("빈푹", "북부", ["Vĩnh Phúc", "Vinh Phuc", "빈푹"]),
    ("푸토", "북부", ["Phú Thọ", "Phu Tho", "푸토"]),
    ("타이응우옌", "북부", ["Thái Nguyên", "Thai Nguyen", "타이응우옌"]),
    ("꽝닌", "북부", ["Quảng Ninh", "Quang Ninh", "꽝닌"]),
    ("타이빈", "북부", ["Thái Bình", "Thai Binh", "타이빈"]),
    ("하남", "북부", ["Hà Nam", "Ha Nam"]),
    ("남딘", "북부", ["Nam Định", "Nam Dinh", "남딘"]),
    ("닌빈", "북부", ["Ninh Bình", "Ninh Binh", "닌빈"]),
    ("탄호아", "중부", ["Thanh Hóa", "Thanh Hoá", "Thanh Hoa", "탄호아"]),
    ("응에안", "중부", ["Nghệ An", "Nghe An", "응에안"]),
    ("하띤", "중부", ["Hà Tĩnh", "Ha Tinh", "하띤"]),
    ("다낭", "중부", ["Đà Nẵng", "Da Nang", "Danang", "다낭"]),
    ("꽝남", "중부", ["Quảng Nam", "Quang Nam", "꽝남"]),
    ("꽝응아이", "중부", ["Quảng Ngãi", "Quang Ngai", "꽝응아이"]),
    ("빈딘", "중부", ["Bình Định", "Binh Dinh", "빈딘"]),
    ("칸호아", "중부", ["Khánh Hòa", "Khánh Hoà", "Khanh Hoa", "칸호아"]),
    ("호치민", "남부", ["TP.HCM", "TP HCM", "TPHCM", "Hồ Chí Minh", "Ho Chi Minh", "HCMC", "호치민", "호찌민"]),
    ("빈즈엉", "남부", ["Bình Dương", "Binh Duong", "빈즈엉"]),
    ("동나이", "남부", ["Đồng Nai", "Dong Nai", "동나이"]),
    ("바리아붕따우", "남부", ["Bà Rịa", "Vũng Tàu", "Ba Ria", "Vung Tau", "붕따우"]),
    ("롱안", "남부", ["Long An", "롱안"]),
    ("떠이닌", "남부", ["Tây Ninh", "Tay Ninh", "떠이닌"]),
    ("빈프억", "남부", ["Bình Phước", "Binh Phuoc", "빈프억"]),
    ("껀터", "남부", ["Cần Thơ", "Can Tho", "껀터"]),
]
REGION_RULES = [(name, zone, _compile(al, case_sensitive=True)) for name, zone, al in REGIONS]

AMOUNT_PATTERNS = [
    (re.compile(r"(\d{1,3}(?:[.,]\d{1,3})*)\s*(triệu|tỷ)\s*(?:USD|đô la Mỹ|đô la|đô)", re.I), "vi"),
    (re.compile(r"(?:US)?\$\s?(\d{1,3}(?:[.,]\d{1,3})*)\s*(million|billion|mln|bln|mn|bn|m|b)(?![A-Za-z])", re.I), "en"),
    (re.compile(r"(\d{1,3}(?:[.,]\d{1,3})*)\s*(million|billion)\s*(?:USD|US dollars|dollars)", re.I), "en"),
    (re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|만)\s*(?:달러|불)"), "ko"),
]
UNIT_MILLIONS = {"triệu": 1, "tỷ": 1000, "million": 1, "mln": 1, "mn": 1, "m": 1,
                 "billion": 1000, "bln": 1000, "bn": 1000, "b": 1000, "억": 100, "만": 0.01}


def _to_float(s, lang):
    if lang == "vi":
        s = s.replace(".", "").replace(",", ".")   # 1,5 tỷ = 1.5 / 1.200 triệu = 1200
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def find_amount(text):
    best = None
    for pat, lang in AMOUNT_PATTERNS:
        for m in pat.finditer(text):
            v = _to_float(m.group(1), lang)
            if v is None:
                continue
            millions = v * UNIT_MILLIONS[m.group(2).lower() if lang != "ko" else m.group(2)]
            if 0.1 <= millions <= 100000 and (best is None or millions > best):
                best = millions
    return best


def fmt_usd(millions):
    if millions is None:
        return ""
    man = round(millions * 100)          # 만 달러 단위
    eok, rest = divmod(man, 10000)
    if eok and rest:
        return f"{eok:,}억 {rest:,}만 달러"
    if eok:
        return f"{eok:,}억 달러"
    return f"{rest:,}만 달러"


def classify(text, cfg, query_topic=None):
    text = nfc(text)
    low = text
    score = sum(w for w, pats in SCORE_RULES for p in pats if p.search(low))
    stage = next(((k, l, h) for k, l, h, pats in STAGE_RULES if _any(pats, low)), OTHER_STAGE)
    regions, zones = [], []
    for name, zone, pats in REGION_RULES:
        if _any(pats, text):
            regions.append(name)
            if zone not in zones:
                zones.append(zone)
    watch = [w for w in cfg.get("watch_keywords", []) if w.lower() in low.lower()]
    if watch:
        score += 3
    if stage[0] in ("construct", "license", "operate"):
        score += 2
    excluded = any(x.lower() in low.lower() for x in cfg.get("exclude_keywords", []))
    if excluded:
        score -= 4
    amount = find_amount(text)
    if amount and amount >= 50:
        score += 1

    hits = []
    for k, _l, _h, pats in TOPIC_RULES:
        n = sum(1 for p in pats if p.search(low))
        if n:
            hits.append((n, k))
    if stage[0] in ("construct", "license", "operate"):
        hits.append((99, "invest"))            # 착공·인허가·준공은 무조건 투자·공장
    hits.sort(key=lambda x: -x[0])
    topics = [k for _, k in hits]
    if hits:
        topic = hits[0][1]
        if query_topic and query_topic in topics and hits[0][0] < 99:
            # 동률이면 검색어 분야 우선
            top_n = hits[0][0]
            if any(n == top_n and k == query_topic for n, k in hits):
                topic = query_topic
        score += 2 * min(hits[0][0], 3) if hits[0][0] < 99 else 0
    else:
        topic = query_topic or "industry"
    if query_topic:
        score += 2
    t_label, t_hint = TOPIC_INFO[topic]
    hint = stage[2] if (topic == "invest" and stage[0] != "other") else t_hint
    return {
        "topic": topic, "topic_label": t_label, "topics": topics,
        "score": score,
        "stage": stage[0], "stage_label": stage[1], "hint": hint,
        "regions": regions, "zones": zones,
        "industries": [l for l, pats in INDUSTRIES if _any(pats, low)],
        "countries": [l for l, pats in COUNTRIES if _any(pats, text)],
        "amount_m": amount, "amount_label": fmt_usd(amount),
        "watch": watch,
    }


# ─────────────────────────── 수집 ───────────────────────────
DEFAULT_TOPIC_QUERIES = {
    "invest": {
        "vi": ["khởi công nhà máy", "động thổ nhà máy", "khánh thành nhà máy",
               "cấp giấy chứng nhận đăng ký đầu tư nhà máy", "dự án FDI nhà máy",
               "khu công nghiệp thu hút đầu tư", "mở rộng nhà máy", "nhà đầu tư Hàn Quốc nhà máy"],
        "en": ["Vietnam factory groundbreaking", "Vietnam new plant investment",
               "Vietnam investment certificate factory", "Vietnam industrial park FDI",
               "Vietnam manufacturing expansion"],
        "ko": ["베트남 공장 착공", "베트남 공장 증설", "베트남 투자허가"]},
    "tariff": {
        "vi": ["thuế quan Việt Nam", "thuế đối ứng Mỹ Việt Nam", "phòng vệ thương mại chống bán phá giá",
               "hiệp định thương mại tự do xuất khẩu", "quy tắc xuất xứ hàng hóa"],
        "en": ["Vietnam tariff", "Vietnam US trade agreement", "Vietnam anti-dumping duty",
               "Vietnam transshipment rules"],
        "ko": ["베트남 관세", "베트남 통상"]},
    "trade": {
        "vi": ["kim ngạch xuất nhập khẩu", "xuất khẩu tăng trưởng tháng", "nhập khẩu nguyên liệu sản xuất"],
        "en": ["Vietnam exports", "Vietnam trade surplus", "Vietnam imports"],
        "ko": ["베트남 수출입"]},
    "logistics": {
        "vi": ["logistics Việt Nam", "cảng biển container sản lượng", "cước vận tải biển",
               "vận tải hàng hóa đường sắt", "vận chuyển hàng hóa hàng không", "trung tâm logistics"],
        "en": ["Vietnam logistics", "Vietnam port container throughput", "Vietnam freight rates shipping",
               "Cai Mep Thi Vai port", "Vietnam air cargo"],
        "ko": ["베트남 물류", "베트남 항만"]},
    "law": {
        "vi": ["nghị định hải quan mới", "thủ tục hải quan", "quy định mới xuất nhập khẩu",
               "thông tư thuế mới", "luật vận tải logistics"],
        "en": ["Vietnam customs regulation", "Vietnam new decree import export", "Vietnam tax law change"],
        "ko": ["베트남 통관 규정", "베트남 법령 개정"]},
    "consumer": {
        "vi": ["thương mại điện tử Việt Nam", "bán lẻ tiêu dùng", "sức mua thị trường", "giao hàng chặng cuối"],
        "en": ["Vietnam e-commerce", "Vietnam retail market", "Vietnam consumer trends"],
        "ko": ["베트남 소비 트렌드", "베트남 이커머스"]},
    "industry": {
        "vi": ["chỉ số sản xuất công nghiệp", "PMI Việt Nam", "ngành dệt may xuất khẩu",
               "ngành điện tử xuất khẩu", "ngành gỗ nội thất xuất khẩu", "ngành thủy sản xuất khẩu"],
        "en": ["Vietnam manufacturing PMI", "Vietnam industry outlook", "Vietnam textile industry",
               "Vietnam electronics industry"],
        "ko": ["베트남 산업 동향", "베트남 제조업"]},
}


def target_langs(cfg=None):
    """번역 대상 언어: 화면 설정 언어 (config의 translate_langs로 강제 가능)"""
    forced = (cfg or {}).get("translate_langs")
    if forced:
        return forced
    lang = load_settings().get("lang", "ko")
    return [lang if lang in ("ko", "en") else "ko"]


def fill_translations(items, targets, limit=200):
    """번역이 없는 기사 제목을 최근 것부터 채움. 채운 개수 반환"""
    n = 0
    for it in sorted(items, key=lambda x: x.get("published") or x.get("fetched") or "", reverse=True):
        for t in targets:
            k = f"title_{t}"
            if it.get(k):
                continue
            if it.get("lang") == t:
                it[k] = it.get("title_orig", "")
                continue
            if n >= limit:
                return n
            res = translate(it.get("title_orig", ""), it.get("lang", "en"), target=t)
            n += 1
            if res:
                it[k] = res
    return n


def topic_queries(cfg):
    tq = cfg.get("topics") or json.loads(json.dumps(DEFAULT_TOPIC_QUERIES))
    inv = tq.setdefault("invest", {})
    for lang in ("vi", "en", "ko"):   # 예전 설정(queries_vi 등)도 투자·공장에 합침
        for q in cfg.get(f"queries_{lang}", []):
            inv.setdefault(lang, [])
            if q not in inv[lang]:
                inv[lang].append(q)
    return tq

def gnews_url(q, lang, days):
    q = quote(f"{q} when:{days}d")
    if lang == "vi":
        return f"https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi"
    if lang == "ko":
        return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s):
    s = TAG_RE.sub(" ", s or "")
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&quot;", '"', s)
    s = re.sub(r"&#39;|&apos;", "'", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_key(title):
    t = nfc(title).lower()
    t = re.sub(r"[^\w]+", "", t)
    return hashlib.md5(t[:80].encode("utf-8")).hexdigest()[:16]


def fetch_feed(url, lang):
    import feedparser
    d = feedparser.parse(url, agent=UA)
    out = []
    for e in d.entries:
        title = nfc(strip_html(e.get("title", "")))
        source = ""
        src = e.get("source")
        if src is not None:
            source = src.get("title", "") if hasattr(src, "get") else ""
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)].strip()
        elif not source and " - " in title and "news.google" in url:
            title, source = title.rsplit(" - ", 1)
        if not source:
            source = d.feed.get("title", "") if hasattr(d, "feed") else ""
        summary = nfc(strip_html(e.get("summary", "")))
        if summary.startswith(title[:30]) or len(summary) > 400:
            summary = summary[:400] if not summary.startswith(title[:30]) else ""
        pub = None
        for k in ("published_parsed", "updated_parsed"):
            if e.get(k):
                pub = dt.datetime(*e[k][:6], tzinfo=dt.timezone.utc).isoformat()
                break
        if title:
            out.append({"title": title, "summary": summary, "link": e.get("link", ""),
                        "source": source, "published": pub, "lang": lang})
    return out


_translator_cache = {}
_engine_fail = {}          # 엔진별 연속 실패 횟수 (3회면 이번 수집에서 제외)
ENGINE_ORDER = ["mymemory"]
MYMEMORY_EMAIL = ""
ENGINE_ROUTES = {}   # 엔진별 접속 경로: "direct" 또는 "http://host:port" (없으면 config의 proxy)


def _opener(route=None):
    import urllib.request
    if route == "direct":
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if route:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": route, "https": route}))
    return urllib.request.build_opener()   # 환경변수(config의 proxy) 사용


def _http_json(url, timeout=15, route=None):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with _opener(route).open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _tr_gtx(text, lang, target="ko", route=None):
    url = (f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={target}&dt=t&q="
           + quote(text[:1800]))
    data = _http_json(url, route=route or ENGINE_ROUTES.get("gtx"))
    out = "".join(seg[0] for seg in data[0] if seg and seg[0])
    if not out:
        raise ValueError("빈 응답")
    return out


def _tr_google(text, lang, target="ko", route=None):
    from deep_translator import GoogleTranslator
    route = route or ENGINE_ROUTES.get("google")
    proxies = {"http": None, "https": None} if route == "direct" else ({"http": route, "https": route} if route else None)
    return GoogleTranslator(source="auto", target=target, proxies=proxies).translate(text[:4500]) or text


def _tr_mymemory(text, lang, target="ko", route=None):
    src = {"vi": "vi", "en": "en", "ko": "ko"}.get(lang, "en")
    url = f"https://api.mymemory.translated.net/get?q={quote(text[:480])}&langpair={src}|{target}"
    if MYMEMORY_EMAIL:
        url += "&de=" + quote(MYMEMORY_EMAIL)   # 이메일 등록 시 하루 한도 약 10배
    data = _http_json(url, route=route or ENGINE_ROUTES.get("mymemory"))
    out = (data.get("responseData") or {}).get("translatedText", "")
    if not out or "MYMEMORY WARNING" in out.upper():
        raise ValueError(out or "빈 응답")
    return out


ENGINES = {"gtx": _tr_gtx, "google": _tr_google, "mymemory": _tr_mymemory}


def translate(text, lang, order=None, target="ko"):
    if not text or lang == target:
        return text
    key = (text, target)
    if key in _translator_cache:
        return _translator_cache[key]
    for name in (order or ENGINE_ORDER):
        if _engine_fail.get(name, 0) >= 3:
            continue
        try:
            res = ENGINES[name](text, lang, target)
            _engine_fail[name] = 0
            _translator_cache[key] = res
            time.sleep(0.2)
            return res
        except Exception as ex:
            _engine_fail[name] = _engine_fail.get(name, 0) + 1
            if _engine_fail[name] == 3:
                log(f"번역 엔진 {name} 제외 (연속 실패): {str(ex)[:80]}")
    return None   # 모두 실패 → 원문 표시, 다음 수집 때 재시도


def load_data():
    if DATA_PATH.exists():
        try:
            with open(DATA_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {"updated": None, "items": []}


def save_data(data):
    tmp = DATA_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    tmp.replace(DATA_PATH)


def collect(cfg=None):
    cfg = cfg or load_config()
    apply_proxy(cfg)
    global ENGINE_ORDER, MYMEMORY_EMAIL
    ENGINE_ORDER = cfg.get("translator_order") or ENGINE_ORDER
    MYMEMORY_EMAIL = cfg.get("mymemory_email", "").strip()
    ENGINE_ROUTES.clear(); ENGINE_ROUTES.update(cfg.get("translator_routes") or {})
    _engine_fail.clear()
    days = int(cfg.get("news_window_days", 2))
    jobs = [(gnews_url(q, lang, days), lang, topic)
            for topic, langs in topic_queries(cfg).items()
            for lang, qs in langs.items() for q in qs]
    jobs += [(u, "auto", None) for u in cfg.get("extra_feeds", [])]
    per_query = int(cfg.get("max_per_query", 15))
    targets = target_langs(cfg)

    data = load_data()
    by_id = {it["id"]: it for it in data["items"]}
    now = dt.datetime.now(dt.timezone.utc)
    new_count, fetched = 0, 0

    for url, lang, qtopic in jobs:
        try:
            entries = fetch_feed(url, lang)[:per_query]
        except Exception as ex:
            log(f"수집 실패 {url[:80]}: {ex}")
            continue
        fetched += len(entries)
        for en in entries:
            if en["lang"] == "auto":
                en["lang"] = "ko" if re.search(r"[가-힣]", en["title"]) else "en"
            iid = norm_key(en["title"])
            if iid in by_id:
                continue
            info = classify(en["title"] + " " + en["summary"], cfg, qtopic)
            if info["score"] < cfg.get("min_score", 3):
                continue
            item = {"id": iid, "query_topic": qtopic, "title_orig": en["title"], "summary_orig": en["summary"],
                    "link": en["link"], "source": en["source"], "lang": en["lang"],
                    "published": en["published"], "fetched": now.isoformat(), **info}
            for t in targets:
                item[f"title_{t}"] = translate(en["title"], en["lang"], target=t)
                item[f"summary_{t}"] = (translate(en["summary"], en["lang"], target=t)
                                        if en["summary"] and cfg.get("translate_summary", False) else "")
            by_id[iid] = item
            new_count += 1

    cutoff = now - dt.timedelta(days=int(cfg.get("keep_days", 7)))

    def ts(it):
        try:
            return dt.datetime.fromisoformat(it.get("published") or it["fetched"])
        except (ValueError, TypeError):
            return now

    items = [it for it in by_id.values() if ts(it) >= cutoff]
    fill_translations(items, targets, limit=150)   # 이전 실패분·언어 변경분 보충
    items.sort(key=ts, reverse=True)
    data = {"updated": now.isoformat(), "items": items}
    save_data(data)
    render_html(data)
    log(f"수집 완료: 피드 {len(jobs)}개, 기사 {fetched}건 확인, 신규 {new_count}건, 보관 {len(items)}건")
    return new_count


def load_checks():
    if CHECKS_PATH.exists():
        try:
            with open(CHECKS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_checks(checks):
    tmp = CHECKS_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(checks, f, ensure_ascii=False, indent=1)
    tmp.replace(CHECKS_PATH)


def export_checks_csv():
    import csv
    checks = load_checks()
    en = load_settings().get("lang") == "en"
    folder = Path.home() / "Downloads"
    if not folder.exists():
        folder = BASE
    path = folder / (f"Vietnam_checklist_{dt.datetime.now():%Y%m%d_%H%M}.csv" if en
                     else f"베트남_체크목록_{dt.datetime.now():%Y%m%d_%H%M}.csv")
    rows = sorted(checks.values(), key=lambda c: c.get("checked_at", ""), reverse=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Checked", "Status", "Topic", "Stage", "Title", "Original title", "Amount (USD M)",
                    "Regions", "Investor", "Industry", "Source", "Published", "Memo", "Link"] if en else
                   ["체크일", "상태", "분야", "단계", "제목(한국어)", "원문 제목", "금액", "지역", "투자국",
                    "산업", "출처", "게재일", "메모", "링크"])
        st_en = {"확인 필요": "To check", "진행 중": "In progress", "완료": "Done"}
        for c in rows:
            it = c.get("item", {})
            w.writerow([(c.get("checked_at") or "")[:10],
                        st_en.get(c.get("status", ""), c.get("status", "")) if en else c.get("status", ""),
                        it.get("topic", "") if en else it.get("topic_label", ""),
                        it.get("stage", "") if en else it.get("stage_label", ""),
                        it.get("title_en" if en else "title_ko") or it.get("title_orig", ""), it.get("title_orig", ""),
                        (f"{it['amount_m']:g}" if it.get("amount_m") else "") if en else it.get("amount_label", ""), ", ".join(it.get("regions", [])),
                        ", ".join(it.get("countries", [])), ", ".join(it.get("industries", [])),
                        it.get("source", ""), (it.get("published") or "")[:10], c.get("memo", ""),
                        it.get("link", "")])
    return path


SETTINGS_PATH = BASE / "settings.json"


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def render_html(data=None):
    data = data or load_data()
    try:
        cfg = load_config()
        for it in data.get("items", []):
            it.update(classify(it.get("title_orig", "") + " " + it.get("summary_orig", ""),
                               cfg, it.get("query_topic")))
    except Exception as ex:
        log(f"재분류 오류: {ex}")
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    payload = json.dumps({**data, "checks": load_checks(), "settings": load_settings()},
                         ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/null", payload)
    if WEB_MODE:
        head = ('<link rel="manifest" href="manifest.webmanifest">\n'
                '<link rel="icon" href="icon-192.png">\n'
                '<link rel="apple-touch-icon" href="icon-192.png">\n'
                '<meta name="theme-color" content="#15263A">\n'
                '<script>window.RADAR_WEB = true;</script>\n</head>')
        html = html.replace("</head>", head, 1)
        SITE_DIR.mkdir(exist_ok=True)
        import shutil
        for f in WEB_DIR.glob("*"):
            shutil.copy2(f, SITE_DIR / f.name)
        (SITE_DIR / "index.html").write_text(html, encoding="utf-8")
        with open(SITE_DIR / "data.json", "w", encoding="utf-8") as f:   # 다음 실행 때 이어받을 데이터
            json.dump(data, f, ensure_ascii=False)
        (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
        return
    HTML_PATH.write_text(html, encoding="utf-8")


# ─────────────────────────── 위젯 ───────────────────────────
def _python_cmd():
    exe = Path(sys.executable)
    return str(exe)   # pythonw.exe면 콘솔 창 없이 실행됨


def run_worker(*args, timeout=1800):
    """수집·번역을 별도 프로세스로 실행 → 위젯 화면이 멈추지 않음"""
    flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
    r = subprocess.run([_python_cmd(), str(BASE / "news_widget.py"), *args],
                       cwd=str(BASE), timeout=timeout, creationflags=flags,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode


def fill_language_cli(lang):
    cfg = load_config()
    apply_proxy(cfg)
    global ENGINE_ORDER, MYMEMORY_EMAIL
    ENGINE_ORDER = cfg.get("translator_order") or ENGINE_ORDER
    MYMEMORY_EMAIL = cfg.get("mymemory_email", "").strip()
    ENGINE_ROUTES.clear(); ENGINE_ROUTES.update(cfg.get("translator_routes") or {})
    _engine_fail.clear()
    data = load_data()
    n = fill_translations(data["items"], [lang], limit=150)
    save_data(data)
    render_html(data)
    log(f"언어 전환 번역: {lang} {n}건")


class Api:
    def __init__(self):
        self.window = None
        self._lock = threading.Lock()
        self._file_lock = threading.Lock()

    def refresh(self):
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "msg": "이미 수집 중입니다"}
        try:
            code = run_worker("--collect")
            return {"ok": code == 0, "msg": "" if code == 0 else "수집 중 오류가 났습니다. radar.log를 확인하세요."}
        except Exception as ex:
            log(f"수집 오류: {ex}")
            return {"ok": False, "msg": str(ex)}
        finally:
            self._lock.release()

    def open(self, url):
        webbrowser.open(url)

    def save_check(self, c):
        with self._file_lock:
            checks = load_checks()
            checks[c["item"]["id"]] = c
            save_checks(checks)
        return True

    def remove_check(self, item_id):
        with self._file_lock:
            checks = load_checks()
            checks.pop(item_id, None)
            save_checks(checks)
        return True

    def fill_language(self, lang):
        """언어를 바꿨을 때 최근 기사 제목을 그 언어로 번역해 채움 (별도 프로세스)"""
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "msg": "busy"}
        try:
            code = run_worker("--fill-lang", "en" if lang == "en" else "ko")
            return {"ok": code == 0}
        except Exception as ex:
            log(f"언어 전환 번역 오류: {ex}")
            return {"ok": False, "msg": str(ex)}
        finally:
            self._lock.release()

    def save_settings(self, st):
        with self._file_lock:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
        return True

    def export_checks(self):
        try:
            path = export_checks_csv()
            if os.name == "nt":
                os.startfile(str(path))
            return {"ok": True, "path": str(path)}
        except Exception as ex:
            log(f"내보내기 오류: {ex}")
            return {"ok": False, "msg": str(ex)}


def _needs_refresh(cfg):
    upd = load_data().get("updated")
    if not upd:
        return True
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(upd)
    return age > dt.timedelta(hours=float(cfg.get("refresh_hours", 6)))


def run_widget():
    cfg = load_config()
    apply_proxy(cfg)
    render_html()
    try:
        import webview
    except ImportError:
        log("pywebview가 없어 브라우저로 엽니다.")
        if _needs_refresh(cfg):
            collect(cfg)
        webbrowser.open(HTML_PATH.as_uri())
        return

    cloud = cfg.get("cloud_url", "").strip()
    w, h = int(cfg.get("widget_width", 440)), int(cfg.get("widget_height", 880))
    if cloud:   # 클라우드 화면을 그대로 띄움 (휴대폰과 같은 내용)
        x = y = None
        try:
            sc = webview.screens[0]
            x, y = max(0, sc.width - w - 24), 24
            h = min(h, sc.height - 80)
        except Exception:
            pass
        webview.create_window("베트남 영업 레이더", cloud, width=w, height=h, x=x, y=y,
                              min_size=(360, 480), on_top=bool(cfg.get("always_on_top", False)))
        webview.start(private_mode=False, storage_path=str(BASE / "webview_data"))
        return

    api = Api()
    x = y = None
    try:
        sc = webview.screens[0]
        x, y = max(0, sc.width - w - 24), 24
        h = min(h, sc.height - 80)
    except Exception:
        pass
    window = webview.create_window("베트남 영업 레이더", str(HTML_PATH), js_api=api,
                                   width=w, height=h, x=x, y=y, min_size=(360, 480),
                                   on_top=bool(cfg.get("always_on_top", False)),
                                   background_color="#E9EEF1")
    api.window = window

    def background():
        interval = float(cfg.get("refresh_hours", 6)) * 3600
        first = True
        while True:
            if not first or _needs_refresh(cfg):
                try:
                    window.evaluate_js("window.radarBusy && window.radarBusy(true)")
                except Exception:
                    pass
                api.refresh()
                try:
                    window.load_url(HTML_PATH.as_uri())
                except Exception:
                    pass
            first = False
            time.sleep(interval)

    def on_start():
        threading.Thread(target=background, daemon=True).start()

    webview.start(on_start, private_mode=False, storage_path=str(BASE / "webview_data"))


def install_startup(remove=False):
    if os.name != "nt":
        print("윈도우에서만 지원합니다.")
        return
    startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    lnk = startup / "VN Investment Radar.lnk"
    if remove:
        if lnk.exists():
            lnk.unlink()
        print("시작 프로그램에서 제거했습니다.")
        return
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    script = BASE / "news_widget.py"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{pyw}';$s.Arguments='\"{script}\" --widget';"
          f"$s.WorkingDirectory='{BASE}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print(f"시작 프로그램에 등록했습니다: {lnk}")


NEWS_TEST_URL = "https://news.google.com/rss/search?q=Vietnam&hl=en-US&gl=US&ceid=US:en"


def apply_proxy(cfg):
    """config.json의 proxy 값을 환경변수로 설정 (feedparser·번역 모두 적용)"""
    px = (cfg or {}).get("proxy", "").strip()
    if px:
        if "://" not in px:
            px = "http://" + px
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ[k] = px


def _try(url, proxy=None, timeout=12):
    import urllib.request
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener.open(req, timeout=timeout) as r:
        return r.status


def _windows_proxy_candidates():
    """윈도우 인터넷 설정(레지스트리)과 자동 구성 스크립트(PAC)에서 프록시 후보 추출"""
    found, info = [], {}
    if os.name != "nt":
        return found, info
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        def val(n):
            try:
                return winreg.QueryValueEx(k, n)[0]
            except OSError:
                return None
        info = {"ProxyEnable": val("ProxyEnable"), "ProxyServer": val("ProxyServer"),
                "AutoConfigURL": val("AutoConfigURL")}
    except Exception:
        pass
    ps = info.get("ProxyServer")
    if ps:
        for part in str(ps).split(";"):
            part = part.split("=", 1)[-1].strip()
            if part:
                found.append(part)
    pac = info.get("AutoConfigURL")
    if pac:
        try:
            import urllib.request
            text = urllib.request.urlopen(pac, timeout=10).read().decode("utf-8", "ignore")
            for m in re.finditer(r"PROXY\s+([\w.\-]+:\d+)", text):
                found.append(m.group(1))
            info["PAC_has_DIRECT"] = "DIRECT" in text
            hints = [ln.strip()[:110] for ln in text.splitlines()
                     if re.search(r"translat|mymemory|google", ln, re.I)]
            if hints:
                info["PAC_rules"] = " | ".join(hints[:4])
        except Exception as ex:
            info["PAC_error"] = str(ex)
    if pac:   # PAC 파일을 주는 서버 자체가 프록시인 경우도 있음
        m = re.match(r"https?://([\w.\-]+):(\d+)", str(pac))
        if m:
            found.append(f"{m.group(1)}:{m.group(2)}")
    uniq = []
    for f in found:
        f = f if "://" in f else "http://" + f
        if f not in uniq:
            uniq.append(f)
    return uniq, info


def _set_env_proxy(px):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if px:
            os.environ[k] = px
        else:
            os.environ.pop(k, None)


def check_network():
    cfg = load_config()
    print("=" * 60)
    configured = cfg.get("proxy", "").strip()
    if configured and "://" not in configured:
        configured = "http://" + configured
    print("Proxy in config.json :", configured or "(none)")
    cands, info = _windows_proxy_candidates()
    for k, v in info.items():
        print(f"Windows {k:<13}: {v}")
    print("Proxy candidates     :", ", ".join(cands) or "(none)")
    print("-" * 60)

    trials = []
    for px in ([configured] if configured else []) + cands + [None]:
        if px not in trials:
            trials.append(px)

    # 1) 뉴스 접속 경로 찾기
    working = "UNSET"
    for px in trials:
        label = px or "direct (no proxy)"
        try:
            st = _try(NEWS_TEST_URL, px)
            print(f"[OK]   Google News via {label} ({st})")
            working = px
            break
        except Exception as ex:
            print(f"[FAIL] Google News via {label}: {str(ex)[:80]}")
    if working == "UNSET":
        print("=" * 60)
        print("RESULT: Could not reach Google News from Python.")
        print("  -> Check if https://news.google.com opens in your browser, and send this screen.")
        return False

    # 2) 번역 엔진별로 경로(프록시 후보 / 직접 연결)를 모두 시험
    _set_env_proxy(working)
    global MYMEMORY_EMAIL
    MYMEMORY_EMAIL = cfg.get("mymemory_email", "").strip()
    routes = []
    for r in [working] + cands + ["direct"]:
        r = r or "direct"
        if r not in routes:
            routes.append(r)
    ok_engines, eng_routes = [], {}
    for name in ["mymemory", "gtx", "google"]:
        if ok_engines:
            break            # 하나라도 되면 나머지(회사에서 막힌 Google)는 시험 생략
        for r in routes:
            try:
                import socket as _s
                old = _s.getdefaulttimeout(); _s.setdefaulttimeout(8)
                try:
                    res = ENGINES[name]("Factory construction starts in Bac Ninh", "en", "ko", r)
                finally:
                    _s.setdefaulttimeout(old)
                print(f"[OK]   Translator {name:<9} via {r}: {res[:30]}")
                ok_engines.append(name); eng_routes[name] = r
                break
            except Exception as ex:
                print(f"[FAIL] Translator {name:<9} via {r}: {str(ex)[:60]}")

    # 3) 설정 저장
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data["proxy"] = (working or "").replace("http://", "")
    if ok_engines:
        data["translator_order"] = ok_engines   # 막힌 엔진은 시도하지 않음
        data["translator_routes"] = eng_routes
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 60)
    print(f"Saved to config.json -> proxy: {data['proxy'] or '(direct)'}")
    if ok_engines:
        print(f"RESULT: OK. News + translation ready (translator: {ok_engines[0]}).")
    else:
        print("RESULT: News OK, but all translators blocked. Titles will show in original language.")
        print("  -> In your browser, open this address and tell me if you see text like {\"responseData\"...}:")
        print("     https://api.mymemory.translated.net/get?q=hello&langpair=en|ko")
        print("  -> Send this screen.")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="베트남 투자 레이더")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--widget", action="store_true")
    ap.add_argument("--install-startup", action="store_true")
    ap.add_argument("--uninstall-startup", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fill-lang")
    ap.add_argument("--web", action="store_true", help="GitHub 클라우드 수집 모드")
    a = ap.parse_args()
    if a.web:
        WEB_MODE = True
        DATA_PATH = BASE / "data" / "news_data.json"
        DATA_PATH.parent.mkdir(exist_ok=True)
        LOG_PATH = BASE / "data" / "radar.log"
    if a.fill_lang:
        fill_language_cli(a.fill_lang)
    elif a.check:
        check_network()
    elif a.collect:
        collect()
    elif a.install_startup:
        install_startup()
    elif a.uninstall_startup:
        install_startup(remove=True)
    else:
        run_widget()
