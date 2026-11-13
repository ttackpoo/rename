
import os
import re
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

TITLE = "한글 파일명 변환기 + 용량별 폴더 분할"
DEFAULT_MB = 450
MB = 1_000_000  # 450MB = 450,000,000바이트

TOKENS = re.compile(r"\$\$|\$(\d+)")


# ==========================================
# 공통 함수
# ==========================================

def key(name):
    return name.casefold()


def split_name(name):
    if name.startswith(".") and name.count(".") == 1:
        return name, ""
    return os.path.splitext(name)


def validate_name(name):
    forbidden = '<>:"/\\|?*'

    if not name or name in (".", ".."):
        raise ValueError("빈 파일명은 사용할 수 없습니다.")

    if any(c in forbidden or ord(c) < 32 for c in name):
        raise ValueError(f"사용할 수 없는 파일명: {name}")

    if name.endswith((" ", ".")):
        raise ValueError(f"끝에 점 또는 공백이 있습니다: {name}")

    base = name.split(".")[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved |= {f"COM{i}" for i in range(1, 10)}
    reserved |= {f"LPT{i}" for i in range(1, 10)}

    if base in reserved:
        raise ValueError(f"Windows 예약 파일명: {name}")


def get_files(folder):
    result = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False) and not entry.is_symlink():
                result.append(entry.name)
    return sorted(result, key=lambda x: (x.casefold(), x))


def size_mb(size):
    return f"{size / MB:,.2f} MB"


def ensure_folder(folder):
    if not os.path.isdir(folder):
        raise ValueError("올바른 작업 폴더를 선택하세요.")


# ==========================================
# PowerRename 방식 정규표현식
# ==========================================

def expand_groups(match, replacement):
    def replace_token(token):
        if token.group(0) == "$$":
            return "$"

        index = int(token.group(1))

        if index > match.re.groups:
            raise ValueError(
                f"${index} 그룹이 존재하지 않습니다. "
                f"사용 가능한 그룹: 0~{match.re.groups}"
            )

        return match.group(index) or ""

    return TOKENS.sub(replace_token, replacement)


def make_target(name, find, replacement,
                prefix, suffix, regex_mode):

    stem, ext = split_name(name)

    if find:
        if regex_mode:
            pattern = re.compile(find)
            stem = pattern.sub(
                lambda m: expand_groups(m, replacement),
                stem
            )
        else:
            stem = stem.replace(find, replacement)

    return prefix + stem + suffix + ext


def unique_name(desired, used):
    stem, ext = split_name(desired)
    candidate = desired
    number = 1

    while key(candidate) in used:
        candidate = f"{stem} ({number}){ext}"
        validate_name(candidate)
        number += 1

    return candidate


def rename_plan(folder, find, replacement,
                prefix, suffix, regex_mode):

    ensure_folder(folder)

    if regex_mode and find:
        re.compile(find)

    files = get_files(folder)
    existing = os.listdir(folder)

    candidates = []

    for old in files:
        new = make_target(
            old, find, replacement,
            prefix, suffix, regex_mode
        )
        validate_name(new)
        candidates.append((old, new))

    # 바뀌지 않는 파일 및 폴더 이름은 보호
    unchanged = {
        key(old)
        for old, new in candidates
        if old == new
    }

    file_keys = {key(f) for f in files}
    protected = {
        key(name)
        for name in existing
        if key(name) not in file_keys
    } | unchanged

    # 변경되는 파일의 원래 이름은
    # 다른 파일의 새 이름으로 사용할 수 있음
    used = set(protected)
    plan = []

    for old, desired in candidates:
        if old == desired:
            continue

        new = unique_name(desired, used)
        used.add(key(new))
        plan.append((old, new))

    return plan


def validate_rename_execution(folder, plan):
    ensure_folder(folder)

    names = os.listdir(folder)
    existing = {key(n) for n in names}
    moving = {key(old) for old, _ in plan}
    targets = [key(new) for _, new in plan]

    if len(targets) != len(set(targets)):
        raise ValueError("변경 대상 이름이 중복됩니다.")

    for old, new in plan:
        path = os.path.join(folder, old)

        if not os.path.isfile(path) or os.path.islink(path):
            raise ValueError(f"원본 파일 없음: {old}")

        if key(new) in existing and key(new) not in moving:
            raise ValueError(f"변경 대상 이름 충돌: {new}")


def execute_rename(folder, plan):
    if not plan:
        return

    validate_rename_execution(folder, plan)

    staged = []
    completed = []

    try:
        # 1단계: 원본 -> 임시 이름
        for old, new in plan:
            temp = ".rename_" + uuid.uuid4().hex + ".tmp"

            while os.path.lexists(os.path.join(folder, temp)):
                temp = ".rename_" + uuid.uuid4().hex + ".tmp"

            os.rename(
                os.path.join(folder, old),
                os.path.join(folder, temp)
            )
            staged.append((old, temp, new))

        # 2단계: 임시 이름 -> 최종 이름
        for old, temp, new in staged:
            os.rename(
                os.path.join(folder, temp),
                os.path.join(folder, new)
            )
            completed.append((old, temp, new))

    except Exception as error:
        failures = []

        for old, temp, new in reversed(completed):
            try:
                os.rename(
                    os.path.join(folder, new),
                    os.path.join(folder, temp)
                )
            except OSError as e:
                failures.append(str(e))

        for old, temp, new in reversed(staged):
            try:
                if os.path.lexists(os.path.join(folder, temp)):
                    os.rename(
                        os.path.join(folder, temp),
                        os.path.join(folder, old)
                    )
            except OSError as e:
                failures.append(str(e))

        msg = f"파일명 변경 실패:\n{error}"

        if failures:
            msg += (
                "\n\n일부 복구 실패. 파일을 직접 확인하세요.\n"
                + "\n".join(failures[:5])
            )

        raise RuntimeError(msg) from error


# ==========================================
# 용량별 폴더 분할
# Best-Fit Decreasing
# ==========================================

def split_plan(folder, limit_mb):
    ensure_folder(folder)

    limit = int(limit_mb * MB)

    if limit <= 0:
        raise ValueError("분할 기준은 0보다 커야 합니다.")

    files = []
    oversized = []

    for name in get_files(folder):
        path = os.path.join(folder, name)
        size = os.path.getsize(path)

        if size > limit:
            oversized.append((name, size))
        else:
            files.append((name, size))

    # 큰 파일부터 처리
    files.sort(key=lambda x: (-x[1], x[0].casefold(), x[0]))

    bins = []

    for name, size in files:
        best = None
        best_space = None

        for i, bucket in enumerate(bins):
            remaining = limit - bucket["size"]

            if size <= remaining:
                space_after = remaining - size

                if best_space is None or space_after < best_space:
                    best = i
                    best_space = space_after

        if best is None:
            bins.append({
                "size": 0,
                "files": []
            })
            best = len(bins) - 1

        bins[best]["files"].append((name, size))
        bins[best]["size"] += size

    # 기존 폴더와 겹치지 않는 새 폴더명 생성
    existing = {key(n) for n in os.listdir(folder)}
    folder_names = []
    number = 1

    for bucket in bins:
        while True:
            candidate = f"분할_{number:03d}"
            number += 1

            if key(candidate) not in existing:
                break

        existing.add(key(candidate))
        folder_names.append(candidate)

    moves = []

    for bucket, target_folder in zip(bins, folder_names):
        for name, size in bucket["files"]:
            moves.append((name, target_folder, size))

    return moves, bins, oversized


def execute_split(folder, moves):
    if not moves:
        return

    ensure_folder(folder)

    # 실행 직전 파일과 대상 폴더 재검사
    target_dirs = sorted({d for _, d, _ in moves})
    originals = {}

    for name, target, expected_size in moves:
        source = os.path.join(folder, name)
        dest_dir = os.path.join(folder, target)

        if not os.path.isfile(source) or os.path.islink(source):
            raise ValueError(f"원본 파일 없음: {name}")

        if os.path.getsize(source) != expected_size:
            raise ValueError(f"파일 용량이 변경되었습니다: {name}")

        if os.path.lexists(dest_dir):
            raise ValueError(f"대상 폴더명이 이미 존재합니다: {target}")

        originals[name] = source

    created = []
    moved = []

    try:
        for dirname in target_dirs:
            path = os.path.join(folder, dirname)
            os.mkdir(path)
            created.append(path)

        for name, dirname, size in moves:
            source = originals[name]
            target = os.path.join(folder, dirname, name)

            if os.path.lexists(target):
                raise ValueError(f"대상 파일 충돌: {target}")

            os.rename(source, target)
            moved.append((source, target))

    except Exception as error:
        failures = []

        for source, target in reversed(moved):
            try:
                if os.path.lexists(source):
                    raise OSError("원본 위치에 파일이 이미 존재함")
                os.rename(target, source)
            except OSError as e:
                failures.append(str(e))

        for path in reversed(created):
            try:
                os.rmdir(path)
            except OSError as e:
                failures.append(str(e))

        msg = f"폴더 분할 실패:\n{error}"

        if failures:
            msg += (
                "\n\n일부 복구 실패. 폴더를 확인하세요.\n"
                + "\n".join(failures[:5])
            )

        raise RuntimeError(msg) from error


def undo_split(folder, moves):
    ensure_folder(folder)

    # 전체 사전 검사
    for name, dirname, size in moves:
        source = os.path.join(folder, dirname, name)
        target = os.path.join(folder, name)

        if not os.path.isfile(source) or os.path.islink(source):
            raise ValueError(f"복구할 파일 없음: {source}")

        if os.path.lexists(target):
            raise ValueError(f"원본 위치에 파일 존재: {name}")

    restored = []

    try:
        for name, dirname, size in moves:
            source = os.path.join(folder, dirname, name)
            target = os.path.join(folder, name)

            os.rename(source, target)
            restored.append((source, target))

    except Exception as error:
        failures = []

        for source, target in reversed(restored):
            try:
                os.rename(target, source)
            except OSError as e:
                failures.append(str(e))

        msg = f"분할 복구 실패:\n{error}"

        if failures:
            msg += (
                "\n\n일부 파일의 원상 복구 실패:\n"
                + "\n".join(failures[:5])
            )

        raise RuntimeError(msg) from error

    # 빈 분할 폴더만 삭제
    for dirname in {d for _, d, _ in moves}:
        try:
            os.rmdir(os.path.join(folder, dirname))
        except OSError:
            pass


# ==========================================
# GUI
# ==========================================

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(TITLE)
        self.root.geometry("1050x740")
        self.root.minsize(800, 580)

        self.folder = tk.StringVar()
        self.find = tk.StringVar()
        self.replacement = tk.StringVar()
        self.prefix = tk.StringVar()
        self.suffix = tk.StringVar()
        self.regex_mode = tk.BooleanVar(value=True)
        self.limit_mb = tk.StringVar(value=str(DEFAULT_MB))

        self.rename_undo = None
        self.split_undo = None
        self.current_split_plan = None

        self.status = tk.StringVar(value="작업 폴더를 선택하세요.")

        self.build_ui()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text=TITLE,
            font=("Malgun Gothic", 17, "bold")
        ).pack(anchor="w", pady=(0, 12))

        folder_frame = ttk.LabelFrame(
            main, text="작업 폴더 선택", padding=10
        )
        folder_frame.pack(fill="x")

        ttk.Entry(
            folder_frame,
            textvariable=self.folder
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        ttk.Button(
            folder_frame,
            text="폴더 선택",
            command=self.choose_folder
        ).pack(side="right")

        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True, pady=12)

        self.rename_tab = ttk.Frame(notebook, padding=10)
        self.split_tab = ttk.Frame(notebook, padding=10)

        notebook.add(self.rename_tab, text="파일명 일괄 변경")
        notebook.add(self.split_tab, text="용량별 폴더 분할")

        self.build_rename_tab()
        self.build_split_tab()

        ttk.Label(
            main,
            textvariable=self.status
        ).pack(anchor="w", pady=5)

    def make_table(self, parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=8)

        names = [f"c{i}" for i in range(len(columns))]

        tree = ttk.Treeview(
            frame,
            columns=names,
            show="headings"
        )

        for col, heading in zip(names, columns):
            tree.heading(col, text=heading)
            tree.column(col, width=180, minwidth=100)

        scroll = ttk.Scrollbar(
            frame, orient="vertical", command=tree.yview
        )
        tree.configure(yscrollcommand=scroll.set)

        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        return tree

    def build_rename_tab(self):
        rules = ttk.LabelFrame(
            self.rename_tab, text="파일명 변경 규칙", padding=10
        )
        rules.pack(fill="x")

        ttk.Checkbutton(
            rules,
            text="정규표현식(RE) 사용: $1, $2 그룹 치환",
            variable=self.regex_mode
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=5)

        items = [
            ("찾을 문자", self.find),
            ("바꿀 문자", self.replacement),
            ("앞에 추가", self.prefix),
            ("뒤에 추가", self.suffix)
        ]

        for i, (label, variable) in enumerate(items, 1):
            ttk.Label(rules, text=label).grid(
                row=i, column=0, sticky="w", pady=4
            )
            ttk.Entry(
                rules, textvariable=variable
            ).grid(
                row=i, column=1, sticky="ew",
                padx=10, pady=4
            )

        rules.columnconfigure(1, weight=1)

        ttk.Label(
            rules,
            text=r"예: (.*)_(\d+)  →  $2_$1    |    $0=전체, $$=달러",
            foreground="gray"
        ).grid(row=5, column=0, columnspan=2, sticky="w")

        buttons = ttk.Frame(self.rename_tab)
        buttons.pack(fill="x", pady=10)

        ttk.Button(
            buttons, text="미리보기",
            command=self.preview_rename
        ).pack(side="left", padx=4)

        ttk.Button(
            buttons, text="파일명 변경 실행",
            command=self.run_rename
        ).pack(side="left", padx=4)

        ttk.Button(
            buttons, text="변경 취소",
            command=self.undo_rename
        ).pack(side="left", padx=4)

        self.rename_tree = self.make_table(
            self.rename_tab,
            ["변경 전 파일명", "변경 후 파일명"]
        )

    def build_split_tab(self):
        options = ttk.LabelFrame(
            self.split_tab, text="용량별 분할 설정", padding=10
        )
        options.pack(fill="x")

        ttk.Label(
            options, text="폴더당 최대 용량 (MB)"
        ).pack(side="left", padx=5)

        ttk.Entry(
            options,
            textvariable=self.limit_mb,
            width=12
        ).pack(side="left", padx=5)

        ttk.Label(
            options,
            text="용량 최적화 / 초과 파일은 원본 유지",
            foreground="gray"
        ).pack(side="left", padx=15)

        buttons = ttk.Frame(self.split_tab)
        buttons.pack(fill="x", pady=10)

        ttk.Button(
            buttons, text="분할 미리보기",
            command=self.preview_split
        ).pack(side="left", padx=4)

        ttk.Button(
            buttons, text="폴더 분할 실행",
            command=self.run_split
        ).pack(side="left", padx=4)

        ttk.Button(
            buttons, text="분할 복구",
            command=self.restore_split
        ).pack(side="left", padx=4)

        self.split_tree = self.make_table(
            self.split_tab,
            ["파일명", "파일 크기", "이동 대상 폴더"]
        )

        self.split_summary = tk.StringVar(value="미리보기 전")
        ttk.Label(
            self.split_tab,
            textvariable=self.split_summary
        ).pack(anchor="w")

    def choose_folder(self):
        folder = filedialog.askdirectory(
            title="작업 폴더 선택"
        )

        if folder:
            self.folder.set(folder)
            self.rename_tree.delete(
                *self.rename_tree.get_children()
            )
            self.split_tree.delete(
                *self.split_tree.get_children()
            )
            self.current_split_plan = None
            self.status.set("작업 폴더 선택 완료")

    def get_folder(self):
        folder = self.folder.get()
        ensure_folder(folder)
        return folder

    # ======================================
    # 파일명 변경 GUI 동작
    # ======================================

    def get_rename_plan(self):
        return rename_plan(
            self.get_folder(),
            self.find.get(),
            self.replacement.get(),
            self.prefix.get(),
            self.suffix.get(),
            self.regex_mode.get()
        )

    def show_rename_plan(self, plan):
        self.rename_tree.delete(
            *self.rename_tree.get_children()
        )

        for old, new in plan:
            self.rename_tree.insert(
                "", "end", values=(old, new)
            )

        self.status.set(f"파일명 변경 대상: {len(plan)}개")

    def preview_rename(self):
        try:
            plan = self.get_rename_plan()
            self.show_rename_plan(plan)
        except Exception as e:
            self.rename_tree.delete(
                *self.rename_tree.get_children()
            )
            messagebox.showerror("미리보기 오류", str(e))

    def run_rename(self):
        try:
            folder = self.get_folder()
            plan = self.get_rename_plan()
            self.show_rename_plan(plan)

            if not plan:
                messagebox.showinfo("안내", "변경할 파일이 없습니다.")
                return

            if not messagebox.askyesno(
                "변경 확인",
                f"{len(plan)}개 파일명을 변경하시겠습니까?"
            ):
                return

            # 다른 종류의 미복구 작업이 있으면
            # 작업 순서에 따른 복구 충돌을 방지
            if self.split_undo:
                raise ValueError(
                    "먼저 직전 폴더 분할을 복구하거나 "
                    "프로그램을 다시 시작하세요."
                )

            # 새 변경을 시작하면 이전 변경 취소 기록 무효화
            self.rename_undo = None
            execute_rename(folder, plan)

            self.rename_undo = (
                folder,
                [(new, old) for old, new in plan]
            )

            self.status.set(f"파일명 변경 완료: {len(plan)}개")
            messagebox.showinfo("완료", "파일명 변경 완료")

        except Exception as e:
            messagebox.showerror("변경 오류", str(e))

    def undo_rename(self):
        if not self.rename_undo:
            messagebox.showinfo(
                "안내", "복구할 파일명 변경 기록이 없습니다."
            )
            return

        if not messagebox.askyesno(
            "변경 취소", "직전 파일명 변경을 복구하시겠습니까?"
        ):
            return

        try:
            folder, plan = self.rename_undo
            execute_rename(folder, plan)

            self.rename_undo = None
            self.rename_tree.delete(
                *self.rename_tree.get_children()
            )
            self.status.set("파일명 변경 복구 완료")
            messagebox.showinfo("완료", "파일명을 복구했습니다.")

        except Exception as e:
            messagebox.showerror("복구 오류", str(e))

    # ======================================
    # 용량별 분할 GUI 동작
    # ======================================

    def get_split_plan(self):
        try:
            limit = float(self.limit_mb.get())
        except ValueError:
            raise ValueError("용량 기준을 숫자로 입력하세요.")

        if not (0 < limit <= 1_000_000):
            raise ValueError("용량 기준이 올바르지 않습니다.")

        return split_plan(self.get_folder(), limit)

    def show_split_plan(self, moves, bins, oversized):
        self.split_tree.delete(
            *self.split_tree.get_children()
        )

        for name, dirname, size in moves:
            self.split_tree.insert(
                "", "end",
                values=(name, size_mb(size), dirname)
            )

        for name, size in oversized:
            self.split_tree.insert(
                "", "end",
                values=(name, size_mb(size), "원본 유지 (초과)")
            )

        total = sum(size for _, _, size in moves)

        self.split_summary.set(
            f"생성 폴더: {len(bins)}개  |  "
            f"이동 파일: {len(moves)}개  |  "
            f"이동 용량: {size_mb(total)}  |  "
            f"초과 파일: {len(oversized)}개"
        )

    def preview_split(self):
        try:
            moves, bins, oversized = self.get_split_plan()
            self.show_split_plan(moves, bins, oversized)
            self.current_split_plan = (
                self.get_folder(),
                moves, bins, oversized
            )
            self.status.set("폴더 분할 미리보기 완료")

        except Exception as e:
            self.current_split_plan = None
            self.split_tree.delete(
                *self.split_tree.get_children()
            )
            messagebox.showerror("분할 미리보기 오류", str(e))

    def run_split(self):
        try:
            folder = self.get_folder()

            # 실행 직전에 최신 목록으로 재계산
            moves, bins, oversized = self.get_split_plan()
            self.show_split_plan(moves, bins, oversized)

            if not moves:
                messagebox.showinfo(
                    "안내", "분할할 파일이 없습니다."
                )
                return

            if self.rename_undo:
                raise ValueError(
                    "먼저 직전 파일명 변경을 취소하거나 "
                    "프로그램을 다시 시작하세요."
                )

            if self.split_undo:
                raise ValueError(
                    "이전 폴더 분할의 복구 기록이 남아 있습니다. "
                    "먼저 분할 복구를 실행하세요."
                )

            msg = (
                f"{len(moves)}개 파일을\n"
                f"{len(bins)}개 폴더로 이동하시겠습니까?\n\n"
                f"초과 파일 {len(oversized)}개는 원본에 유지됩니다."
            )

            if not messagebox.askyesno("분할 확인", msg):
                return

            execute_split(folder, moves)
            self.split_undo = (folder, moves)

            self.status.set(
                f"분할 완료: {len(bins)}개 폴더"
            )
            messagebox.showinfo(
                "완료", "용량별 폴더 분할 완료"
            )

        except Exception as e:
            messagebox.showerror("분할 오류", str(e))

    def restore_split(self):
        if not self.split_undo:
            messagebox.showinfo(
                "안내", "복구할 분할 작업이 없습니다."
            )
            return

        if not messagebox.askyesno(
            "분할 복구",
            "분할된 파일을 원래 폴더로 복구하시겠습니까?"
        ):
            return

        try:
            folder, moves = self.split_undo
            undo_split(folder, moves)

            self.split_undo = None
            self.current_split_plan = None

            self.split_tree.delete(
                *self.split_tree.get_children()
            )
            self.split_summary.set("분할 복구 완료")
            self.status.set("분할 복구 완료")

            messagebox.showinfo(
                "완료", "파일을 원래 폴더로 복구했습니다."
            )

        except Exception as e:
            messagebox.showerror("복구 오류", str(e))


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
