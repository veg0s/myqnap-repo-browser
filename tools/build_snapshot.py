#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 myqnap.org 的 repo.xml 转成站点用的紧凑快照 data/snapshot.json。

用法：
    python tools/build_snapshot.py                       # 直接从源站拉（4.4MB，未压缩）
    python tools/build_snapshot.py --local repo.xml      # 用本地已下载的 XML
    python tools/build_snapshot.py --self-check          # 生成后额外做一致性断言
    python tools/build_snapshot.py --dry-run             # 只统计不写文件

约定（改前端前务必先看）：
  * items 顺序必须与 XML 完全一致 —— 前端用数组下标当 id，详情弹层、锚点都依赖它。
  * 字段名与 XML 同名（publishedDate 带空格、description 保留原始 HTML），
    只有 platforms 压成三元组 [platformID, location, signature]。
  * pidArch 是「架构多数票」结果，前端流式加载时拿它当种子，收尾再全量重算。
"""
import argparse
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

REPO_URL = "https://myqnap.org/repo.xml"
ARCH_FILE_RE = re.compile(r"_(x86_64|arm_64|arm-x41|arm-x19|arm-x31|x86)\.qpkg(?:\?|$)", re.I)
LEGACY_32_RE = re.compile(r"OLD|32\s*-?\s*BIT", re.I)

FIELDS = [
    "name", "internalName", "version", "publishedDate", "type", "category", "language",
    "maintainer", "developer", "changeLog", "forumlink", "snapshot",
    "icon80", "icon100", "bannerImg", "tutorialLink", "description",
]


def fetch(url, timeout=90):
    """显式要求 identity：源站支持 br，但标准库不会解 br，绝不能拿到 br 却按 gzip 解。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "mqrepo-snapshot/1.0 (+github pages)",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        enc = (resp.headers.get("Content-Encoding") or "identity").lower()
    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc not in ("identity", ""):
        raise SystemExit("源站返回了不支持的编码 %r，请改用 --local 下载后的 XML" % enc)
    return raw.decode("utf-8")


def fetch_with_retries(url, timeout, retries):
    """CI 里经常碰到 Cloudflare 偶发 52x / 连接重置，重试比让定时任务空跑一天划算。"""
    last = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            return fetch(url, timeout)
        except SystemExit:
            raise
        except Exception as exc:                      # noqa: BLE001 - 网络/HTTP 一律重试
            last = exc
            print("第 %d/%d 次下载失败：%s" % (attempt, retries, exc), file=sys.stderr)
            if attempt < retries:
                time.sleep(min(30, 4 * attempt))
    raise SystemExit("源站连续 %d 次不可用，最后一次错误：%s" % (retries, last))


def text_of(el, tag):
    node = el.find(tag)
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def norm_pid(raw):
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s:
        return "UNKNOWN"
    up = s.upper()
    if "X86" in up and LEGACY_32_RE.search(up):
        return "X86_32_LEGACY"
    pid = re.sub(r"\s+", "", re.sub(r"TS-NAS-?ARM-?64", "TS-NASARM_64", re.sub(r"TS-NAS-?X86", "TS-NASX86", up)))
    return pid


def arch_from_location(url):
    m = ARCH_FILE_RE.search(url or "")
    if not m:
        return ""
    a = m.group(1).lower()
    return "x86_32" if a == "x86" else a


def build(root):
    items = []
    votes = {}
    for el in root:
        if el.tag != "item":
            continue
        platforms = []
        for p in el.findall("platform"):
            pid = text_of(p, "platformID")
            loc = text_of(p, "location")
            sig = text_of(p, "signature")
            platforms.append([pid, loc, sig])
            np = norm_pid(pid)
            fa = arch_from_location(loc)
            if fa:
                votes.setdefault(np, Counter())[fa] += 1
        row = {k: text_of(el, k) for k in FIELDS}
        row["name"] = row["name"] or "(未命名)"
        row["platforms"] = platforms
        items.append(row)

    pid_arch = {}
    for pid, counter in votes.items():
        pid_arch[pid] = counter.most_common(1)[0][0]
    return items, pid_arch


def same_content(path, doc):
    """items 与 cachechk 都没变就别重写：generatedAt 每次都不同，直接写会导致天天有大提交。"""
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            old = json.load(fh)
    except Exception:
        return False
    if old.get("v") != doc["v"] or old.get("cachechk") != doc["cachechk"]:
        return False
    if old.get("count") != doc["count"]:
        return False
    old_items = json.dumps(old.get("items"), ensure_ascii=False, separators=(",", ":"))
    new_items = json.dumps(doc["items"], ensure_ascii=False, separators=(",", ":"))
    return old_items == new_items


def read_generated_at(path):
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("generatedAt") or ""
    except Exception:
        return ""


def self_check(items, pid_arch):
    problems = []
    if len(items) < 700:
        problems.append("条目数偏少：%d" % len(items))
    ids = set()
    for i, it in enumerate(items):
        if not it["platforms"]:
            problems.append("第 %d 条（%s）没有 platform" % (i, it["name"]))
        for pid, loc, _sig in it["platforms"]:
            if not loc.endswith(".qpkg"):
                problems.append("第 %d 条链接不以 .qpkg 结尾：%s" % (i, loc[:80]))
        key = it["name"] + "|" + it["version"]
        if key in ids:
            problems.append("疑似重复条目：%s %s" % (it["name"], it["version"]))
        ids.add(key)
    if not pid_arch:
        problems.append("pidArch 为空")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=REPO_URL)
    ap.add_argument("--local", help="本地 repo.xml 路径（跳过网络）")
    ap.add_argument("--out", default=os.path.join("data", "snapshot.json"))
    ap.add_argument("--status-out", default=os.path.join("data", "snapshot.status.json"),
                    help="每次运行都写的小状态文件（lastRun / changed），供页面显示最近检查时间；传空串则不写")
    ap.add_argument("--force", action="store_true", help="即使内容未变也重写快照")
    ap.add_argument("--min-items", type=int, default=700)
    ap.add_argument("--timeout", type=int, default=90, help="单次下载超时秒数")
    ap.add_argument("--retries", type=int, default=3, help="下载失败重试次数（CI 建议 3）")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--strict", action="store_true", help="自检有问题时不写文件、以非零码退出（CI 用）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.local:
        with open(args.local, "r", encoding="utf-8") as fh:
            xml_text = fh.read()
        source = "file://" + os.path.abspath(args.local)
        print("读取本地：%s（%.1f MB）" % (args.local, len(xml_text.encode("utf-8")) / 1048576))
    else:
        xml_text = fetch_with_retries(args.url, args.timeout, args.retries)
        source = args.url
        print("下载完成：%.1f MB" % (len(xml_text.encode("utf-8")) / 1048576))

    root = ET.fromstring(xml_text)
    if root.tag != "plugins":
        raise SystemExit("根元素不是 <plugins>，实际为 <%s>" % root.tag)

    cachechk = text_of(root, "cachechk")
    items, pid_arch = build(root)
    print("cachechk：%s" % (cachechk or "(无)"))
    print("条目：%d，platform：%d，pidArch：%d 个" % (
        len(items), sum(len(i["platforms"]) for i in items), len(pid_arch)))

    if len(items) < args.min_items:
        raise SystemExit("条目数 %d < --min-items %d，疑似抓到了半截或错误页面" % (len(items), args.min_items))

    doc = {
        "v": 1,
        "source": source,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "cachechk": cachechk,
        "count": len(items),
        "pidArch": pid_arch,
        "items": items,
    }
    body = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    print("JSON：%.2f MB（gzip 约 %.2f MB）" % (
        len(body.encode("utf-8")) / 1048576,
        len(gzip.compress(body.encode("utf-8"), 6)) / 1048576))

    if args.self_check:
        problems = self_check(items, pid_arch)
        if problems:
            print("自检发现 %d 个问题：" % len(problems))
            for p in problems[:20]:
                print("  -", p)
            if len(problems) > 20:
                print("  ...")
            if args.strict:
                raise SystemExit("--strict：自检未通过，已放弃写入")
        else:
            print("自检通过")

    if args.dry_run:
        print("--dry-run：未写文件")
        return

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    changed = True
    if not args.force and same_content(args.out, doc):
        changed = False
        print("内容与现有快照一致，未改写 %s（CI 用 --force 强制覆盖）" % args.out)
    else:
        with io.open(args.out, "w", encoding="utf-8") as fh:
            fh.write(body)
        print("已写入 %s（%.2f MB）" % (args.out, os.path.getsize(args.out) / 1048576))

    # 瘦身状态文件：每次都写（几百字节），既能让页面知道「最近一次检查」时间，
    # 也能让 CI 每天有一次提交，从而避免 GitHub 在 60 天无提交后自动停用定时任务。
    if args.status_out:
        status = {
            "v": 1,
            "lastRun": doc["generatedAt"],
            "cachechk": cachechk,
            "count": len(items),
            "changed": changed,
            "snapshotGeneratedAt": doc["generatedAt"] if changed else read_generated_at(args.out),
        }
        os.makedirs(os.path.dirname(args.status_out) or ".", exist_ok=True)
        with io.open(args.status_out, "w", encoding="utf-8") as fh:
            json.dump(status, fh, ensure_ascii=False, separators=(",", ":"))
        print("状态：%s（changed=%s）" % (args.status_out, changed))


if __name__ == "__main__":
    main()
