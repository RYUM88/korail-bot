"""예매 버튼 탐색과 클릭 (Playwright).

페이지 마크업은 명절마다 바뀔 수 있으므로 두 가지 경로를 모두 제공한다.

1. ``--selector`` 로 정확한 셀렉터를 직접 지정 (권장, ``probe`` 로 미리 확인)
2. 지정하지 않으면 키워드로 클릭 가능한 요소를 훑어 점수가 가장 높은 것을 고름

두 경로 모두 iframe 안까지 뒤진다. 코레일 페이지는 예매 영역을 프레임으로 감싸는
경우가 있어서다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "ButtonCandidate",
    "ClickOutcome",
    "DEFAULT_EXCLUDE_KEYWORDS",
    "DEFAULT_INCLUDE_KEYWORDS",
    "DEFAULT_MIN_SCORE",
    "TicketPage",
    "wait_until_closed",
]


def wait_until_closed(context, *, timeout: float = 3600.0, poll: float = 0.5) -> bool:
    """사용자가 브라우저를 닫을 때까지 기다린다.

    ``input()`` 대신 이걸 쓴다. 표준입력이 없는 환경(예: 에이전트가 대신 실행)에서도
    막히지 않고, 사용자는 하던 대로 창을 닫으면 된다.

    Returns:
        브라우저가 닫혀서 끝났으면 ``True``, 제한 시간을 다 썼으면 ``False``.
    """
    deadline = time.monotonic() + timeout
    closed = {"value": False}
    try:
        context.on("close", lambda *_: closed.__setitem__("value", True))
    except Exception:  # noqa: BLE001 - 이벤트를 못 걸어도 아래 폴링으로 감지한다
        pass
    while time.monotonic() < deadline:
        if closed["value"]:
            return True
        try:
            if not context.pages:
                return True
        except Exception:  # noqa: BLE001 - 컨텍스트가 이미 사라졌다
            return True
        time.sleep(poll)
    return False

DEFAULT_INCLUDE_KEYWORDS: Sequence[str] = (
    "승차권 예매",
    "승차권예매",
    "예매하기",
    "예매 하기",
    "예매",
)
"""앞쪽에 있을수록 가중치가 높다."""

DEFAULT_EXCLUDE_KEYWORDS: Sequence[str] = (
    "예매확인",
    "예매 확인",
    "예매내역",
    "예매 내역",
    "예매취소",
    "취소",
    "환불",
    "반환",
    "조회",
    "안내",
    "공지",
)
"""라벨에 이 단어가 있으면 후보에서 제외한다.

'예매 안내' 같은 링크는 예매 버튼 바로 옆에 있으면서 키워드도 품고 있어서,
실수로 누르면 그날의 기회를 날린다. 그래서 기본값에서 확실히 배제한다."""

DEFAULT_MIN_SCORE: float = 60.0
"""자동 탐색으로 클릭을 허용할 최소 점수.

키워드를 품고만 있는 약한 후보(예: 부가 설명 링크)를 누르느니 아무것도 누르지
않는 편이 낫다. 진짜 버튼이 아직 비활성일 때 엉뚱한 링크로 흘러내리는 것을 막는다."""

# 후보 수집 로직. probe(목록)와 pick(요소 1개) 양쪽에서 같은 소스를 공유한다.
_FINDER_SRC = r"""
function korailFindCandidates(args) {
  const include = args.include || [];
  const exclude = args.exclude || [];
  const requireEnabled = !!args.requireEnabled;
  const requireVisible = !!args.requireVisible;
  const minScore = typeof args.minScore === 'number' ? args.minScore : 0;

  const SELECTOR = [
    'a', 'button',
    'input[type="button"]', 'input[type="submit"]', 'input[type="image"]',
    '[role="button"]', '[onclick]',
    'div[class*="btn"]', 'span[class*="btn"]', 'p[class*="btn"]', 'li[class*="btn"]',
    'div[class*="button"]', 'span[class*="button"]'
  ].join(',');

  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();

  function labelOf(el) {
    const tag = el.tagName.toLowerCase();
    const parts = [];
    if (tag === 'input') parts.push(el.value || '');
    parts.push(el.innerText || el.textContent || '');
    parts.push(el.getAttribute('title') || '');
    parts.push(el.getAttribute('alt') || '');
    parts.push(el.getAttribute('aria-label') || '');
    const img = el.querySelector ? el.querySelector('img[alt]') : null;
    if (img) parts.push(img.getAttribute('alt') || '');
    return norm(parts.filter(Boolean).join(' '));
  }

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (parseFloat(style.opacity || '1') === 0) return false;
    return true;
  }

  function isEnabled(el) {
    if (el.disabled === true) return false;
    if (el.getAttribute('aria-disabled') === 'true') return false;
    const cls = (el.className && el.className.baseVal !== undefined)
      ? el.className.baseVal : (el.className || '');
    if (typeof cls === 'string' && /(^|[\s_-])(disabled|dimmed|off|inactive)([\s_-]|$)/i.test(cls)) {
      return false;
    }
    return true;
  }

  function cssPath(el) {
    const chain = [];
    let node = el;
    for (let depth = 0; node && node.nodeType === 1 && depth < 6; depth++) {
      if (node.id) { chain.unshift('#' + CSS.escape(node.id)); break; }
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          (c) => c.tagName === node.tagName);
        if (siblings.length > 1) {
          part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
        }
      }
      chain.unshift(part);
      node = node.parentElement;
    }
    return chain.join(' > ');
  }

  const scored = [];
  const elements = Array.from(document.querySelectorAll(SELECTOR));
  for (const el of elements) {
    const label = labelOf(el);
    if (!label) continue;
    if (exclude.some((word) => word && label.includes(word))) continue;

    let score = 0;
    let matched = null;
    let longestKeyword = 0;
    for (let i = 0; i < include.length; i++) {
      const word = include[i];
      if (word && label.includes(word)) {
        score += (include.length - i) * 10;
        if (matched === null) matched = word;
        if (word.length > longestKeyword) longestKeyword = word.length;
      }
    }
    if (matched === null) continue;

    // 키워드가 라벨의 얼마나 큰 부분을 차지하는가.
    // '예매'를 품고만 있는 링크와, 라벨 자체가 '승차권 예매'인 버튼을 가른다.
    const coverage = Math.min(1, longestKeyword / Math.max(label.length, 1));
    score += coverage * 30;

    const visible = isVisible(el);
    const enabled = isEnabled(el);
    if (requireVisible && !visible) continue;
    if (requireEnabled && !enabled) continue;

    if (visible) score += 15;
    if (enabled) score += 8;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' || tag === 'button' || tag === 'input') score += 5;
    // 라벨이 길면 버튼 자체가 아니라 그것을 감싼 영역일 가능성이 높다.
    score -= Math.min(label.length / 8, 10);

    if (score < minScore) continue;

    const rect = el.getBoundingClientRect();
    scored.push({
      el: el,
      score: score,
      desc: {
        score: Math.round(score * 10) / 10,
        matched: matched,
        tag: tag,
        label: label.length > 120 ? label.slice(0, 117) + '...' : label,
        id: el.id || null,
        cls: (typeof el.className === 'string' ? el.className : '') || null,
        href: el.getAttribute ? (el.getAttribute('href') || null) : null,
        onclick: el.getAttribute ? (el.getAttribute('onclick') || null) : null,
        visible: visible,
        enabled: enabled,
        cssPath: cssPath(el),
        rect: {
          x: Math.round(rect.x), y: Math.round(rect.y),
          w: Math.round(rect.width), h: Math.round(rect.height)
        }
      }
    });
  }

  // 다른 후보를 품고 있는 바깥 요소는 버린다(가장 안쪽의 실제 버튼만 남긴다).
  const innermost = scored.filter(
    (c) => !scored.some((o) => o !== c && c.el.contains(o.el)));

  innermost.sort((a, b) => b.score - a.score);
  return innermost;
}
"""

_JS_LIST = (
    "(args) => {"
    + _FINDER_SRC
    + " return korailFindCandidates(args).map((c) => c.desc); }"
)

_JS_PICK = (
    "(args) => {"
    + _FINDER_SRC
    + " const found = korailFindCandidates(args); return found.length ? found[0].el : null; }"
)

_JS_HIGHLIGHT = """
(el) => {
  el.style.outline = '4px solid #ff2d55';
  el.style.outlineOffset = '2px';
  el.scrollIntoView({block: 'center', inline: 'center'});
}
"""


@dataclass
class ButtonCandidate:
    """탐색된 클릭 후보 하나."""

    frame_url: str
    data: Dict[str, Any]

    @property
    def score(self) -> float:
        return float(self.data.get("score", 0.0))

    def summary(self) -> str:
        data = self.data
        state = []
        if not data.get("visible"):
            state.append("숨김")
        if not data.get("enabled"):
            state.append("비활성")
        flags = f" [{'/'.join(state)}]" if state else ""
        return (
            f"score={data.get('score'):>6}  <{data.get('tag')}>{flags} "
            f"'{data.get('label')}'\n"
            f"        css : {data.get('cssPath')}\n"
            f"        id  : {data.get('id')}   href: {data.get('href')}\n"
            f"        on  : {data.get('onclick')}"
        )


@dataclass
class ClickOutcome:
    """클릭 시도 결과."""

    clicked: bool
    attempts: int = 0
    mode: Optional[str] = None
    label: Optional[str] = None
    css_path: Optional[str] = None
    frame_url: Optional[str] = None
    clicked_at_mono: Optional[float] = None
    errors: List[str] = field(default_factory=list)
    new_pages: List[str] = field(default_factory=list)
    final_url: Optional[str] = None
    frame_urls: List[str] = field(default_factory=list)
    dry_run: bool = False


class TicketPage:
    """예매 페이지를 열어두고, 정해진 순간에 버튼을 누르는 래퍼."""

    def __init__(
        self,
        context,
        page,
        url: str,
        *,
        selector: Optional[str] = None,
        include: Sequence[str] = DEFAULT_INCLUDE_KEYWORDS,
        exclude: Sequence[str] = DEFAULT_EXCLUDE_KEYWORDS,
        min_score: float = DEFAULT_MIN_SCORE,
        log=print,
    ) -> None:
        self.context = context
        self.page = page
        self.url = url
        self.selector = selector
        self.include = list(include)
        self.exclude = list(exclude)
        self.min_score = min_score
        self.log = log
        self._preferred_frame = None
        self._new_pages: List[Any] = []
        context.on("page", self._on_new_page)

    # -- 내부 ------------------------------------------------------------

    def _on_new_page(self, page) -> None:
        self._new_pages.append(page)

    def _finder_args(
        self,
        *,
        require_enabled: bool,
        require_visible: bool,
        min_score: float = 0.0,
    ) -> Dict[str, Any]:
        return {
            "include": self.include,
            "exclude": self.exclude,
            "requireEnabled": require_enabled,
            "requireVisible": require_visible,
            "minScore": min_score,
        }

    def _frames(self) -> List[Any]:
        """탐색 대상 프레임 목록. 이전에 성공한 프레임을 맨 앞에 둔다."""
        frames = [f for f in self.page.frames if not f.is_detached()]
        if self._preferred_frame is not None and self._preferred_frame in frames:
            frames.remove(self._preferred_frame)
            frames.insert(0, self._preferred_frame)
        return frames

    # -- 페이지 조작 -----------------------------------------------------

    def open(self, *, timeout: float = 60.0, wait_until: str = "domcontentloaded") -> None:
        """대상 URL 을 연다."""
        self.page.goto(self.url, timeout=timeout * 1000, wait_until=wait_until)

    def reload(self, *, timeout: float = 30.0, wait_until: str = "domcontentloaded") -> bool:
        """페이지를 새로고침한다. 실패해도 예외를 올리지 않는다."""
        try:
            self.page.reload(timeout=timeout * 1000, wait_until=wait_until)
            return True
        except Exception as exc:  # noqa: BLE001 - 새로고침 실패는 치명적이지 않다
            self.log(f"  ! 새로고침 실패: {type(exc).__name__}: {exc}")
            return False

    # -- 후보 탐색 -------------------------------------------------------

    def candidates(
        self, *, require_enabled: bool = False, require_visible: bool = False
    ) -> List[ButtonCandidate]:
        """모든 프레임에서 후보를 모아 점수 내림차순으로 돌려준다."""
        args = self._finder_args(
            require_enabled=require_enabled,
            require_visible=require_visible,
            min_score=0.0,
        )
        found: List[ButtonCandidate] = []
        for frame in self._frames():
            try:
                for item in frame.evaluate(_JS_LIST, args) or []:
                    found.append(ButtonCandidate(frame_url=frame.url, data=item))
            except Exception:  # noqa: BLE001 - 이동 중인 프레임은 건너뛴다
                continue
        found.sort(key=lambda c: c.score, reverse=True)
        return found

    def resolve(self, *, require_enabled: bool = True, require_visible: bool = True):
        """지금 클릭할 요소 핸들을 찾는다. 없으면 ``None``.

        프레임마다 후보를 뽑은 뒤 **전체에서 점수가 가장 높은 것**을 고른다.
        메인 프레임에 '예매 안내' 같은 약한 후보가 있어도, iframe 안의 진짜
        예매 버튼에 밀리도록 하기 위해서다.

        Returns:
            ``(element_handle, frame, 설명 dict)`` 또는 ``None``.
        """
        if self.selector:
            return self._resolve_selector()

        args = self._finder_args(
            require_enabled=require_enabled,
            require_visible=require_visible,
            min_score=self.min_score,
        )
        best_frame = None
        best_desc: Dict[str, Any] = {}
        best_score = None
        for frame in self._frames():
            try:
                items = frame.evaluate(_JS_LIST, args)
            except Exception:  # noqa: BLE001 - 이동 중인 프레임은 건너뛴다
                continue
            if not items:
                continue
            top = items[0]
            score = float(top.get("score", 0.0))
            if best_score is None or score > best_score:
                best_frame, best_score, best_desc = frame, score, top

        if best_frame is None:
            return None

        try:
            handle = best_frame.evaluate_handle(_JS_PICK, args)
        except Exception:  # noqa: BLE001
            return None
        element = handle.as_element()
        if element is None:
            return None

        self._preferred_frame = best_frame
        return element, best_frame, best_desc

    def _resolve_selector(self):
        """``--selector`` 로 지정된 요소를 찾는다. 보이는 것을 우선한다."""
        fallback = None
        for frame in self._frames():
            try:
                locator = frame.locator(self.selector).first
                if locator.count() == 0:
                    continue
                element = locator.element_handle(timeout=1000)
            except Exception:  # noqa: BLE001
                continue
            if element is None:
                continue
            desc = {"label": self.selector, "cssPath": self.selector}
            try:
                visible = element.is_visible()
            except Exception:  # noqa: BLE001
                visible = False
            if visible:
                self._preferred_frame = frame
                return element, frame, desc
            if fallback is None:
                fallback = (element, frame, desc)
        if fallback is not None:
            self._preferred_frame = fallback[1]
        return fallback

    def highlight_best(self) -> Optional[Dict[str, Any]]:
        """지금 눌릴 요소를 빨간 테두리로 표시한다(리허설 확인용)."""
        resolved = self.resolve(require_enabled=False, require_visible=False)
        if resolved is None:
            return None
        element, _frame, desc = resolved
        try:
            element.evaluate(_JS_HIGHLIGHT)
        except Exception:  # noqa: BLE001
            pass
        return desc

    # -- 클릭 ------------------------------------------------------------

    def _click_once(self, element, mode: str) -> str:
        """한 번 클릭한다. 성공하면 사용된 방식 이름을 돌려준다."""
        if mode in ("auto", "real"):
            try:
                element.scroll_into_view_if_needed(timeout=1000)
            except Exception:  # noqa: BLE001 - 스크롤 실패는 무시하고 클릭을 시도
                pass
            try:
                element.click(timeout=2000)
                return "real"
            except Exception:
                if mode == "real":
                    raise
        if mode in ("auto", "dom"):
            try:
                element.evaluate("el => el.click()")
                return "dom"
            except Exception:
                if mode == "dom":
                    raise
        element.dispatch_event("click")
        return "dispatch"

    def fire(
        self,
        *,
        mode: str = "auto",
        retry_window: float = 20.0,
        retry_interval: float = 0.3,
        fast_interval: float = 0.02,
        fast_window: float = 2.0,
        reload_after: float = 1.0,
        require_enabled: bool = True,
        dry_run: bool = False,
    ) -> ClickOutcome:
        """버튼을 누른다. 아직 없거나 비활성이면 제한 시간 안에서 재시도한다.

        예매 시작 직후 몇 백 밀리초는 순위를 가르는 구간이라, 처음
        ``fast_window`` 초 동안은 ``fast_interval`` 간격으로 촘촘히 다시 본다.
        이 구간의 재시도는 이미 열려 있는 페이지의 DOM 을 다시 훑는 것뿐이라
        서버로 요청이 나가지 않는다.

        Args:
            mode: ``auto`` | ``real`` | ``dom`` | ``dispatch``.
            retry_window: 재시도 총 제한 시간(초).
            retry_interval: 평상시 재시도 간격(초).
            fast_interval: 초반 촘촘한 재시도 간격(초).
            fast_window: 촘촘한 재시도를 유지할 시간(초).
            reload_after: 후보를 못 찾은 채 이만큼 지나면 새로고침(0이면 안 함).
            require_enabled: 비활성 상태의 버튼을 건너뛸지 여부.
            dry_run: 실제로 누르지 않고 대상만 표시한다.
        """
        outcome = ClickOutcome(clicked=False, dry_run=dry_run)
        started = time.monotonic()
        deadline = started + retry_window
        fast_until = started + fast_window
        last_reload = started
        missing_since: Optional[float] = None

        while True:
            outcome.attempts += 1
            resolved = self.resolve(
                require_enabled=require_enabled, require_visible=True
            )

            if resolved is None:
                now = time.monotonic()
                if missing_since is None:
                    missing_since = now
                outcome.errors.append("클릭 대상을 찾지 못함")
                if (
                    reload_after > 0
                    and now - max(missing_since, last_reload) >= reload_after
                    and now < deadline
                ):
                    self.log("  · 버튼이 아직 없어 새로고침합니다")
                    self.reload()
                    last_reload = time.monotonic()
                    missing_since = None
            else:
                element, frame, desc = resolved
                outcome.label = desc.get("label")
                outcome.css_path = desc.get("cssPath")
                outcome.frame_url = frame.url
                if dry_run:
                    try:
                        element.evaluate(_JS_HIGHLIGHT)
                    except Exception:  # noqa: BLE001
                        pass
                    outcome.clicked = False
                    outcome.mode = "dry-run"
                    outcome.clicked_at_mono = time.monotonic()
                    return outcome
                try:
                    used = self._click_once(element, mode)
                    outcome.clicked = True
                    outcome.mode = used
                    outcome.clicked_at_mono = time.monotonic()
                    return outcome
                except Exception as exc:  # noqa: BLE001 - 다음 시도로 넘어간다
                    outcome.errors.append(f"{type(exc).__name__}: {exc}")

            now = time.monotonic()
            if now >= deadline:
                return outcome
            time.sleep(fast_interval if now < fast_until else retry_interval)

    # -- 사후 확인 -------------------------------------------------------

    def settle(self, wait: float = 5.0) -> ClickOutcome:
        """클릭 뒤 새 창/이동이 생겼는지 잠시 지켜본다."""
        result = ClickOutcome(clicked=True)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and not self._new_pages:
            time.sleep(0.1)
        for page in list(self._new_pages):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:  # noqa: BLE001
                pass
            result.new_pages.append(page.url)
            try:
                page.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
        try:
            result.final_url = self.page.url
            result.frame_urls = [
                frame.url
                for frame in self.page.frames
                if not frame.is_detached() and frame.url != self.page.url
            ]
        except Exception:  # noqa: BLE001
            result.final_url = None
        return result
