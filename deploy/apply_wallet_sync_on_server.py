#!/usr/bin/env python3
"""
在 video-downloader 部署目录内应用「AI 总结扣费后顶栏积分同步」补丁。
默认根目录：/opt/video-downloader

用法（在服务器上）：
  sudo python3 deploy/apply_wallet_sync_on_server.py
  sudo python3 deploy/apply_wallet_sync_on_server.py --root /opt/video-downloader

幂等：已打过补丁则跳过对应片段。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable, Optional


def patch_until_stable(path: Path, mutators: list[Callable[[str], Optional[str]]]) -> bool:
    changed_any = False
    for mut in mutators:
        raw = path.read_text(encoding="utf-8")
        new = mut(raw)
        if new is not None and new != raw:
            path.write_text(new, encoding="utf-8", newline="\n")
            changed_any = True
    return changed_any


def mutate_repair_summarize_decimal(t: str) -> str | None:
    """修复旧版补丁使用 int() 导致 Ab 已支持小数积分（如 0.29）时 SSE 仍传整数。"""
    orig = t
    pairs = [
        (
            'json.dumps({"credits_balance": int(_wb)}, ensure_ascii=False)',
            'json.dumps({"credits_balance": round(float(_wb), 4)}, ensure_ascii=False)',
        ),
        (
            'quota_info["credits_balance"] = int(cb)',
            'quota_info["credits_balance"] = round(float(cb), 4)',
        ),
    ]
    for a, b in pairs:
        if a in t:
            t = t.replace(a, b)
    return t if t != orig else None


def mutate_summarize_early_wallet(t: str) -> str | None:
    if "_wb = consume.get(\"credits_balance\")" in t and 'event="wallet"' in t:
        return None
    needle = """                        "upgrade_vip": reason == "need_membership",
                    },
                    ensure_ascii=False,
                ),
                event="error",
            )
            return

        full_text = subtitle_data["full_text"]"""
    insert = """                        "upgrade_vip": reason == "need_membership",
                    },
                    ensure_ascii=False,
                ),
                event="error",
            )
            return

        # 扣费成功后立刻推送余额，不必等整段总结流结束（否则用户误以为未扣分）
        _wb = consume.get("credits_balance")
        if _wb is not None:
            try:
                yield ServerSentEvent(
                    raw_data=json.dumps({"credits_balance": round(float(_wb), 4)}, ensure_ascii=False),
                    event="wallet",
                )
            except (TypeError, ValueError):
                pass

        full_text = subtitle_data["full_text"]"""
    if needle not in t:
        return None
    return t.replace(needle, insert, 1)


def mutate_summarize_py(t: str) -> str | None:
    marker = 'quota_info["credits_balance"]'
    if marker in t:
        return None
    needle = """        else:
            quota_info = {"remaining": -1, "limit": -1}
        yield ServerSentEvent(
            raw_data=json.dumps(quota_info, ensure_ascii=False),
            event="quota",
        )"""
    insert = """        else:
            quota_info = {"remaining": -1, "limit": -1}
        # 主站 try-consume 在 allowed 时会带回 credits_balance；供前端等客户端与扣费后余额对齐
        cb = consume.get("credits_balance")
        if cb is not None:
            try:
                quota_info["credits_balance"] = round(float(cb), 4)
            except (TypeError, ValueError):
                pass
        yield ServerSentEvent(
            raw_data=json.dumps(quota_info, ensure_ascii=False),
            event="quota",
        )"""
    if needle not in t:
        return None
    return t.replace(needle, insert, 1)


def mutate_app_wallet_poll_interval(t: str) -> str | None:
    if "const WALLET_POLL_MS = 15000" not in t:
        return None
    return t.replace("const WALLET_POLL_MS = 15000", "const WALLET_POLL_MS = 5000", 1)


def mutate_app_vue(t: str) -> str | None:
    changed = False
    if "syncWalletFromAb" not in t:
        old_imp = "import { getSavedUser, fetchMe, logout as logoutApi, goToAbLogin } from './api/auth.js'"
        new_imp = "import { getSavedUser, fetchMe, logout as logoutApi, goToAbLogin, saveUser } from './api/auth.js'"
        if old_imp in t:
            t = t.replace(old_imp, new_imp, 1)
            changed = True

    if "@wallet-sync=\"syncWalletFromAb\"" not in t and "@wallet-sync='syncWalletFromAb'" not in t:
        old_vs = """                @show-pricing="scrollToPricing"
              />"""
        new_vs = """                @show-pricing="scrollToPricing"
                @wallet-sync="syncWalletFromAb"
              />"""
        if old_vs in t:
            t = t.replace(old_vs, new_vs, 1)
            changed = True

    if "async function syncWalletFromAb" not in t:
        needle = """async function restoreUser() {
  const saved = getSavedUser()
  if (saved) currentUser.value = saved
  try {
    currentUser.value = await fetchMe()
  } catch {
    currentUser.value = null
  }
}

// ===== 视频功能 ====="""
        insert = """async function restoreUser() {
  const saved = getSavedUser()
  if (saved) currentUser.value = saved
  try {
    currentUser.value = await fetchMe()
  } catch {
    currentUser.value = null
  }
}

/** AI 总结 SSE：quota 中带 credits_balance 时先即时刷新顶栏；流结束时再拉 /api/auth/me 与主站对齐 */
async function syncWalletFromAb(patch) {
  if (!currentUser.value) return
  if (patch && typeof patch.credits === 'number' && Number.isFinite(patch.credits)) {
    const next = { ...currentUser.value, credits: Math.round(patch.credits * 10000) / 10000 }
    currentUser.value = next
    saveUser(next)
    return
  }
  try {
    currentUser.value = await fetchMe()
  } catch {
    /* 保留当前展示，避免误清空会话 */
  }
}

// ===== 视频功能 ====="""
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True

    return t if changed else None


def mutate_video_summary_wallet_handler(t: str) -> str | None:
    if "wallet: (data)" in t:
        return None
    needle = """      mindmap: (data) => {
        try {
          const parsed = JSON.parse(data)
          mindmapMarkdown.value = parsed.markdown || ''
        } catch (e) { /* ignore parse error */ }
      },
      quota: (data) => {"""
    insert = """      mindmap: (data) => {
        try {
          const parsed = JSON.parse(data)
          mindmapMarkdown.value = parsed.markdown || ''
        } catch (e) { /* ignore parse error */ }
      },
      wallet: (data) => {
        try {
          const o = JSON.parse(data)
          const bal = o?.credits_balance
          if (typeof bal === 'number' && Number.isFinite(bal)) {
            emit('wallet-sync', { credits: Math.round(bal * 10000) / 10000 })
          }
        } catch { /* ignore */ }
      },
      quota: (data) => {"""
    if needle not in t:
        return None
    return t.replace(needle, insert, 1)


def mutate_video_summary_vue(t: str) -> str | None:
    changed = False
    old_emit = "const emit = defineEmits(['loading-change', 'need-login', 'show-pricing'])"
    new_emit = "const emit = defineEmits(['loading-change', 'need-login', 'show-pricing', 'wallet-sync'])"
    if old_emit in t:
        t = t.replace(old_emit, new_emit, 1)
        changed = True

    old_quota_done = """      quota: (data) => {
        try { quotaInfo.value = JSON.parse(data) } catch {}
      },
      done: () => {
        loading.value = false
      },"""

    new_quota_done = """      quota: (data) => {
        try {
          quotaInfo.value = JSON.parse(data)
          const bal = quotaInfo.value?.credits_balance
          if (typeof bal === 'number' && Number.isFinite(bal)) {
            emit('wallet-sync', { credits: Math.round(bal * 10000) / 10000 })
          }
        } catch {}
      },
      done: () => {
        loading.value = false
        emit('wallet-sync')
      },"""

    if old_quota_done in t:
        t = t.replace(old_quota_done, new_quota_done, 1)
        changed = True

    return t if changed else None


def mutate_repair_app_vue_decimal(t: str) -> str | None:
    old = "credits: Math.floor(patch.credits)"
    new = "credits: Math.round(patch.credits * 10000) / 10000"
    if old not in t:
        return None
    return t.replace(old, new, 1)


def mutate_repair_video_summary_decimal(t: str) -> str | None:
    orig = t
    t = t.replace(
        "emit('wallet-sync', { credits: Math.floor(bal) })",
        "emit('wallet-sync', { credits: Math.round(bal * 10000) / 10000 })",
    )
    return t if t != orig else None


SUMMARIZE_MUTATORS = [
    mutate_repair_summarize_decimal,
    mutate_summarize_early_wallet,
    mutate_summarize_py,
]
APP_MUTATORS = [
    mutate_repair_app_vue_decimal,
    mutate_app_vue,
    mutate_app_wallet_poll_interval,
]
VIDEO_MUTATORS = [
    mutate_repair_video_summary_decimal,
    mutate_video_summary_vue,
    mutate_video_summary_wallet_handler,
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root",
        default="/opt/video-downloader",
        help="video-downloader 项目根目录（含 backend/、frontend/）",
    )
    args = ap.parse_args()
    root = Path(args.root).resolve()

    path_mutators: list[tuple[Path, list[Callable[[str], Optional[str]]]]] = [
        (root / "backend/api_summarize.py", SUMMARIZE_MUTATORS),
        (root / "frontend/src/App.vue", APP_MUTATORS),
        (root / "frontend/src/components/VideoSummary.vue", VIDEO_MUTATORS),
    ]

    any_change = False
    missing = []
    checked: list[Path] = []

    for p, mutators in path_mutators:
        if not p.is_file():
            missing.append(str(p))
            continue
        checked.append(p)
        if patch_until_stable(p, mutators):
            print(f"OK patched {p}")
            any_change = True

    if missing:
        print("ERROR missing files:")
        for m in missing:
            print(f"  {m}")
        raise SystemExit(2)

    if not any_change:
        py_ok = (root / "backend/api_summarize.py").read_text(encoding="utf-8")
        app_ok = (root / "frontend/src/App.vue").read_text(encoding="utf-8")
        vs_ok = (root / "frontend/src/components/VideoSummary.vue").read_text(encoding="utf-8")
        has_early_wallet = '_wb = consume.get("credits_balance")' in py_ok and 'event="wallet"' in py_ok
        has_quota_cb = 'quota_info["credits_balance"]' in py_ok
        has_wallet_fe = "wallet: (data)" in vs_ok
        poll_ok = "const WALLET_POLL_MS = 15000" not in app_ok
        if (
            has_early_wallet
            and has_quota_cb
            and "syncWalletFromAb" in app_ok
            and "@wallet-sync=\"syncWalletFromAb\"" in app_ok
            and "'wallet-sync'" in vs_ok
            and "emit('wallet-sync')" in vs_ok
            and has_wallet_fe
            and poll_ok
        ):
            print("Already applied (markers present). Nothing to do.")
            raise SystemExit(0)
        print("ERROR: patch anchors did not match server files. Compare with repo or edit manually:")
        for p in checked:
            print(f"  {p}")
        raise SystemExit(1)

    print("Done. Rebuild containers, e.g.:")
    print(f"  cd {root} && docker compose build web backend && docker compose up -d --force-recreate web backend")


if __name__ == "__main__":
    main()
