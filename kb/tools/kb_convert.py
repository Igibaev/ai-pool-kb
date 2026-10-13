"""Переводит материалы из kb/inbox в текст: kb/inbox/_md/.

Вызывается из `python3 kb/tools/kb.py convert [папка] [--ocr] [--force]`.
Оригиналы не меняет. Только стандартная библиотека; pypdf, openpyxl, py7zr — необязательные.
Агент на любой модели читает один формат: текст с номерами строк.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".log", ".html", ".htm",
            ".py", ".sql", ".js", ".ts", ".java", ".kt", ".cs", ".go", ".bsl", ".bas", ".vbs", ".ps1", ".sh",
            ".bat", ".cmd", ".ini", ".cfg", ".conf", ".properties"}
SUBTITLE_EXT = {".vtt", ".srt"}
ARCHIVE_EXT = {".zip", ".7z"}
NOT_CONVERTED = {
    ".doc": "старый формат Word: попросите .docx или PDF с текстом",
    ".xls": "старый формат Excel: попросите .xlsx или CSV",
    ".rar": "архив RAR: попросите ZIP или распакуйте сами",
    ".ppt": "старый формат PowerPoint: попросите .pptx или PDF",
    ".png": "картинка: модель без зрения её не прочитает — приложите описание словами",
    ".jpg": "картинка: модель без зрения её не прочитает — приложите описание словами",
    ".jpeg": "картинка: модель без зрения её не прочитает — приложите описание словами",
    ".gif": "картинка: модель без зрения её не прочитает — приложите описание словами",
    ".bmp": "картинка: модель без зрения её не прочитает — приложите описание словами",
    ".mp3": "аудио: сначала расшифровка в текст",
    ".m4a": "аудио: сначала расшифровка в текст",
    ".wav": "аудио: сначала расшифровка в текст",
    ".mp4": "видео: сначала расшифровка в текст",
    ".mov": "видео: сначала расшифровка в текст",
}
SCAN_CHARS = 20          # меньше символов на страницу — считаем сканом
XLSX_PREVIEW_ROWS = 50
MEETING_WORDS = ("встреч", "созвон", "запись", "транскриб", "расшифров", "meeting", "call", "transcript", "zoom", "teams")
STATE = ".state.json"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


# ---------- признаки расшифровки встречи ----------

SPEAKER_RE = re.compile(r"^\s*(?:\[?\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?\]?\s*[-–]?\s*)?"
                        r"(Спикер\s*\d+|Speaker\s*\d+|Участник\s*\d+|[А-ЯЁA-Z][\wЁё.\-]*(?:\s[А-ЯЁA-Z][\wЁё.\-]*){0,2})\s*:\s+\S")
TIME_RE = re.compile(r"(?:\[\d{1,2}:\d{2}(?::\d{2})?\]|\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d{1,3})?\b|-->)")


def meeting_signs(text: str, name: str, ext: str) -> list[str]:
    lines = [l for l in text.splitlines() if l.strip()]
    signs = []
    if ext in SUBTITLE_EXT:
        signs.append(f"формат {ext}")
    if lines:
        sp = sum(1 for l in lines if SPEAKER_RE.match(l))
        if sp >= 5 and sp >= 0.1 * len(lines):
            signs.append(f"реплики с метками говорящих: {sp}")
        tm = sum(1 for l in lines if TIME_RE.search(l))
        if tm >= 5:
            signs.append(f"метки времени: {tm}")
    low = name.lower()
    hit = [w for w in MEETING_WORDS if w in low]
    if hit:
        signs.append(f"в имени файла: {hit[0]}")
    return signs


def is_meeting(signs: list[str]) -> bool:
    return len(signs) >= 2


# ---------- чтение форматов ----------

def read_text_file(p: Path) -> tuple[str, str]:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc), ("" if enc == "utf-8-sig" else "перекодирован из cp1251")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "битые символы заменены"


def pdf_pages(p: Path) -> tuple[list[str] | None, str]:
    """Текст по страницам. None — нечем читать."""
    try:
        from pypdf import PdfReader
        try:
            reader = PdfReader(str(p))
            return [(pg.extract_text() or "") for pg in reader.pages], "pypdf"
        except Exception as e:  # битый или зашифрованный PDF
            return [], f"pypdf не открыл файл: {e}"
    except ImportError:
        pass
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(p), "-"], capture_output=True)
        if r.returncode == 0:
            pages = r.stdout.decode("utf-8", errors="replace").split("\f")
            if pages and not pages[-1].strip():
                pages = pages[:-1]
            return pages, "pdftotext"
        return [], "pdftotext не открыл файл"
    return None, "нет pypdf и pdftotext"


def ocr_pdf(p: Path, tmp: Path) -> Path | None:
    if not shutil.which("ocrmypdf"):
        return None
    out = tmp / (p.stem + ".ocr.pdf")
    r = subprocess.run(["ocrmypdf", "-l", "rus+kaz", "--skip-text", "--quiet", str(p), str(out)], capture_output=True)
    return out if r.returncode == 0 and out.exists() else None


def docx_md(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(f"{W}body")
    out = []

    def para_text(el) -> str:
        parts = []
        for node in el.iter():
            if node.tag == f"{W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{W}tab":
                parts.append("\t")
            elif node.tag in (f"{W}br", f"{W}cr"):
                parts.append("\n")
        return "".join(parts)

    def heading_level(el) -> int:
        ps = el.find(f"{W}pPr/{W}pStyle")
        val = (ps.get(f"{W}val") if ps is not None else "") or ""
        m = re.search(r"(?:heading|заголовок|Heading)\s*(\d)", val, flags=re.I)
        return int(m.group(1)) if m else 0

    for el in list(body) if body is not None else []:
        if el.tag == f"{W}p":
            t = para_text(el).strip()
            if not t:
                continue
            lvl = heading_level(el)
            out.append(("#" * min(lvl + 1, 6) + " " + t) if lvl else t)
            out.append("")
        elif el.tag == f"{W}tbl":
            rows = []
            for tr in el.findall(f"{W}tr"):
                cells = [" ".join(para_text(p).strip() for p in tc.findall(f"{W}p")).replace("|", "\\|").replace("\n", " ")
                         for tc in tr.findall(f"{W}tc")]
                rows.append(cells)
            out += md_table(rows)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def pptx_md(p: Path) -> str:
    out = []
    with zipfile.ZipFile(p) as z:
        slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        for i, n in enumerate(slides, 1):
            root = ET.fromstring(z.read(n))
            out.append(f"## слайд {i}")
            for para in root.iter(f"{A}p"):
                t = "".join(x.text or "" for x in para.iter(f"{A}t")).strip()
                if t:
                    out.append(t)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def md_table(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out


def slug(s: str) -> str:
    s = re.sub(r"[^\w.-]+", "-", s.strip(), flags=re.U).strip("-")
    return s or "list"


def subtitles_md(text: str) -> str:
    out, cur_time = [], ""
    for line in text.splitlines():
        l = line.strip()
        if not l or l == "WEBVTT" or l.isdigit() or l.startswith(("NOTE", "STYLE", "Kind:", "Language:")):
            continue
        m = re.match(r"(\d{1,2}:)?(\d{2}:\d{2})[.,]\d+\s*-->", l)
        if m:
            cur_time = (m.group(1) or "") + m.group(2)
            continue
        l = re.sub(r"<[^>]+>", "", l)
        out.append(f"[{cur_time}] {l}" if cur_time else l)
    return "\n".join(out) + "\n"


# ---------- одна единица ----------

class Result:
    def __init__(self, src: str, out: str = "", status: str = "", note: str = "", meeting: list | None = None):
        self.src, self.out, self.status, self.note, self.meeting = src, out, status, note, meeting or []


def header(orig_rel: str, fmt: str, extra: str, status: str, meeting: list[str], today: str) -> str:
    lines = [f"# {Path(orig_rel).name}",
             f"Оригинал: {orig_rel} · Формат: {fmt}" + (f" · {extra}" if extra else "") + f" · Конвертировано: {today} kb.py convert",
             f"Статус: {status}"]
    if is_meeting(meeting):
        lines.append("Тип: похоже на расшифровку встречи (" + "; ".join(meeting) + ")")
    return "\n".join(lines) + "\n\n"


def convert_one(p: Path, orig_rel: str, out_dir: Path, rel_out: str, ocr: bool, today: str, tmp: Path) -> list[Result]:
    ext = p.suffix.lower()
    name = p.name
    target = out_dir / rel_out
    target.parent.mkdir(parents=True, exist_ok=True)

    def write_md(body: str, fmt: str, extra: str, status: str, meeting: list[str]) -> Result:
        md = target.with_name(target.name + ".md") if ext not in (".md",) else target
        md.write_text(header(orig_rel, fmt, extra, status, meeting, today) + body, encoding="utf-8", newline="\n")
        return Result(orig_rel, str(md), status, meeting=meeting)

    if ext in ARCHIVE_EXT:
        return convert_archive(p, orig_rel, out_dir, rel_out, ocr, today, tmp)
    if ext == ".pdf":
        pages, how = pdf_pages(p)
        if pages is None:
            return [write_md("НЕ ПРОЧИТАН: поставьте pypdf (`pip install pypdf`) или poppler (`pdftotext`) и запустите convert снова.\n",
                             "pdf", "", "не прочитан", [])]
        scan = [i for i, t in enumerate(pages, 1) if len(t.strip()) < SCAN_CHARS]
        note = ""
        if pages and len(scan) == len(pages) and ocr:
            o = ocr_pdf(p, tmp)
            if o:
                pages, how = pdf_pages(o)
                scan = [i for i, t in enumerate(pages or [], 1) if len(t.strip()) < SCAN_CHARS]
                note = "распознано ocrmypdf — цифры сверяйте с оригиналом"
            else:
                note = "ocrmypdf не найден"
        if not pages:
            return [write_md(f"НЕ ПРОЧИТАН: {how}.\n", "pdf", "", "не прочитан", [])]
        if len(scan) == len(pages):
            body = ("СКАН: текста нет. Нужна текстовая версия от бизнеса или `python3 kb/tools/kb.py convert --ocr` "
                    "(нужен ocrmypdf с языками rus и kaz). Содержимое не угадывать.\n")
            r = write_md(body, "pdf", f"Страниц: {len(pages)}", "скан без текста", [])
            r.note = note
            return [r]
        body = "".join(f"## стр. {i}\n\n{t.strip()}\n\n" for i, t in enumerate(pages, 1))
        status = "текст" if not scan else f"частично (страницы {', '.join(map(str, scan))} — сканы)"
        text = "\n".join(pages)
        r = write_md(body, "pdf", f"Страниц: {len(pages)}", status, meeting_signs(text, name, ext))
        r.note = note
        return [r]
    if ext == ".docx":
        try:
            body = docx_md(p)
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
            return [write_md(f"НЕ ПРОЧИТАН: файл не открылся как .docx ({e}).\n", "docx", "", "не прочитан", [])]
        return [write_md(body, "docx", "", "текст", meeting_signs(body, name, ext))]
    if ext == ".pptx":
        try:
            body = pptx_md(p)
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
            return [write_md(f"НЕ ПРОЧИТАН: файл не открылся как .pptx ({e}).\n", "pptx", "", "не прочитан", [])]
        return [write_md(body, "pptx", "", "текст", [])]
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            return [write_md("НЕ ПРОЧИТАН: для Excel нужен openpyxl (`pip install openpyxl`), или сохраните листы в CSV.\n",
                             "xlsx", "", "не прочитан", [])]
        try:
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        except Exception as e:
            return [write_md(f"НЕ ПРОЧИТАН: файл не открылся как Excel ({e}).\n", "xlsx", "", "не прочитан", [])]
        parts, results = [], []
        for ws in wb.worksheets:
            rows = [["" if v is None else str(v).replace("\n", " ") for v in r] for r in ws.iter_rows(values_only=True)]
            while rows and not any(c.strip() for c in rows[-1]):
                rows.pop()
            csv_path = target.with_name(f"{target.name}.{slug(ws.title)}.csv")
            buf = io.StringIO()
            csv.writer(buf).writerows(rows)
            csv_path.write_text(buf.getvalue(), encoding="utf-8", newline="")
            head = rows[0] if rows else []
            parts.append(f"## лист «{ws.title}»\n\nСтрок данных: {max(len(rows) - 1, 0)} · Колонки: {', '.join(head) or '—'}\n"
                         f"Весь лист: {csv_path.name} — большие таблицы считайте скриптом или `kb.py peek`\n\n"
                         + "\n".join(md_table([[c.replace('|', '\\|') for c in r] for r in rows[:XLSX_PREVIEW_ROWS + 1]]))
                         + ("\n\n… первые " + str(XLSX_PREVIEW_ROWS) + " строк" if len(rows) > XLSX_PREVIEW_ROWS + 1 else "") + "\n\n")
            results.append(Result(orig_rel, str(csv_path), "лист целиком"))
        r = write_md("".join(parts), "xlsx", f"Листов: {len(wb.worksheets)}", "текст", [])
        return [r] + results
    if ext in SUBTITLE_EXT:
        text, enc = read_text_file(p)
        body = subtitles_md(text)
        return [write_md(body, ext[1:], enc, "текст", meeting_signs(text, name, ext))]
    if ext in TEXT_EXT or ext == "":
        text, enc = read_text_file(p)
        signs = meeting_signs(text, name, ext)
        if is_meeting(signs) and ext in (".txt", ".md", ""):
            return [write_md(text.rstrip() + "\n", ext[1:] or "текст", enc, "текст", signs)]
        target.write_text(text, encoding="utf-8", newline="")
        return [Result(orig_rel, str(target), "текст, копия как есть" + (f"; {enc}" if enc else ""), meeting=signs)]
    why = NOT_CONVERTED.get(ext, f"формат {ext or 'без расширения'} не читается: попросите PDF, DOCX, XLSX или текст")
    return [write_md(f"НЕ КОНВЕРТИРУЕТСЯ: {why}.\n", ext[1:] or "?", "", "не конвертируется", [])]


def _zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def convert_archive(p: Path, orig_rel: str, out_dir: Path, rel_out: str, ocr: bool, today: str, tmp: Path) -> list[Result]:
    ext = p.suffix.lower()
    dest = tmp / f"arc-{abs(hash(orig_rel))}"
    dest.mkdir(parents=True, exist_ok=True)
    if ext == ".zip":
        try:
            with zipfile.ZipFile(p) as z:
                for info in z.infolist():
                    nm = _zip_name(info)
                    if info.is_dir() or nm.startswith(("__MACOSX/", "/")) or ".." in Path(nm).parts:
                        continue
                    target = dest / nm
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(info))
        except zipfile.BadZipFile as e:
            return [Result(orig_rel, "", "не прочитан", f"не ZIP: {e}")]
    else:
        try:
            import py7zr
            with py7zr.SevenZipFile(p, "r") as z:
                z.extractall(dest)
        except ImportError:
            exe = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
            if not exe:
                return [Result(orig_rel, "", "не прочитан", "для .7z нужен py7zr (`pip install py7zr`) или программа 7z")]
            r = subprocess.run([exe, "x", "-y", f"-o{dest}", str(p)], capture_output=True)
            if r.returncode != 0:
                return [Result(orig_rel, "", "не прочитан", "7z не распаковал архив")]
        except Exception as e:
            return [Result(orig_rel, "", "не прочитан", f"py7zr не распаковал архив: {e}")]
    results = []
    for f in sorted(x for x in dest.rglob("*") if x.is_file()):
        inner = f.relative_to(dest).as_posix()
        results += convert_one(f, f"{orig_rel} → {inner}", out_dir, f"{rel_out}/{inner}", ocr, today, tmp)
    if not results:
        results.append(Result(orig_rel, "", "пустой архив"))
    return results


# ---------- команда ----------

def run(args: list[str], kb) -> int:
    ocr = "--ocr" in args
    force = "--force" in args
    pos = [a for a in args if not a.startswith("--")]
    src = Path(pos[0]) if pos else kb.KB / "inbox"
    if not src.is_absolute():
        src = (Path.cwd() / src).resolve()
    if not src.is_dir():
        print(f"Нет папки {pos[0] if pos else 'kb/inbox'}. Положите материалы в kb/inbox/")
        return 1
    out_dir = src / "_md"
    out_dir.mkdir(exist_ok=True)
    state_p = out_dir / STATE
    try:
        state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
    except json.JSONDecodeError:
        state = {}
    today = kb.TODAY.isoformat()
    results, skipped = [], 0

    def show(path: Path) -> str:
        try:
            return kb.rel(path)
        except ValueError:
            return str(path)

    files = sorted(f for f in src.rglob("*") if f.is_file()
                   and not any(part.startswith(("_", ".")) for part in f.relative_to(src).parts))
    with tempfile.TemporaryDirectory() as td:
        for f in files:
            rel = f.relative_to(src).as_posix()
            st = f.stat()
            key = [st.st_size, int(st.st_mtime)]
            if not force and state.get(rel) == key:
                skipped += 1
                continue
            res = convert_one(f, show(f), out_dir, rel, ocr, today, Path(td))
            state[rel] = key
            results += res
    state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    if not results:
        print(f"Нового нет: файлов без изменений — {skipped}. Заново всё: convert --force")
        return 0
    print("| Файл | Результат | Статус |\n|---|---|---|")
    for r in results:
        out = show(Path(r.out)) if r.out else "—"
        st = r.status + (f"; {r.note}" if r.note else "")
        if is_meeting(r.meeting):
            st += "; ПОХОЖЕ НА РАСШИФРОВКУ ВСТРЕЧИ"
        print(f"| {r.src} | {out} | {st} |")
    bad = [r for r in results if r.status in ("скан без текста", "не прочитан", "не конвертируется")]
    meetings = [r for r in results if is_meeting(r.meeting)]
    print(f"\nКонвертировано: {len(results)}; без изменений: {skipped}. Оригиналы в {show(src)} не тронуты.")
    if bad:
        print(f"НЕ ПРОЧИТАНО: {len(bad)} — скажите человеку, какие файлы, и не угадывайте их содержимое.")
    if meetings:
        print(f"ВСТРЕЧИ: {len(meetings)} — оформить в kb/materials/meetings/ с саммари (kb/materials/README.md, «Встречи»).")
    print("Дальше: агент читает только kb/inbox/_md/ и копирует нужное в kb/materials/.")
    return 0
