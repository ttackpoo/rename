
import os
import re
import uuid
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "한글 파일명 변환기 - PowerRename 방식"

# PowerRename 스타일의 $1, $2, $0, $$ 처리
TOKEN_PATTERN = re.compile(r"\$\$|\$(\d+)")


def split_name(filename):
    """파일명과 확장자 분리"""
    if filename.startswith(".") and filename.count(".") == 1:
        return filename, ""
    return os.path.splitext(filename)


def name_key(name):
    """Windows 대소문자 구분 없는 비교"""
    return name.casefold()


def validate_name(name):
    forbidden = '<>:"/\\|?*'

    if not name or name in (".", ".."):
        raise ValueError("파일명이 비어 있습니다.")

    if any(c in forbidden or ord(c) < 32 for c in name):
        raise ValueError(f"사용할 수 없는 문자: {name}")

    if name.endswith((" ", ".")):
        raise ValueError(f"끝에 공백 또는 점이 있습니다: {name}")

    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"COM{i}" for i in range(1, 10))
    reserved.update(f"LPT{i}" for i in range(1, 10))

    if name.split(".")[0].upper() in reserved:
        raise ValueError(f"Windows 예약 이름: {name}")


def get_files(folder):
    result = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False) and not entry.is_symlink():
                result.append(entry.name)
    return sorted(result, key=str.casefold)


def power_replace(match, replacement):
    """
    PowerRename 스타일 그룹 참조:
    $0 = 전체 매치
    $1 = 첫 번째 그룹
    $2 = 두 번째 그룹
    $$ = 문자 $
    """

    def expand_token(token):
        text = token.group(0)

        if text == "$$":
            return "$"

        index = int(token.group(1))

        if index > match.re.groups:
            raise ValueError(
                f"${index} 그룹이 존재하지 않습니다.\n"
                f"정규식 그룹 수: {match.re.groups}"
            )

        return match.group(index) or ""

    return TOKEN_PATTERN.sub(expand_token, replacement)


def make_target(filename, find_text, replace_text,
                prefix, suffix, use_regex):

    stem, ext = split_name(filename)

    if find_text:
        if use_regex:
            try:
                pattern = re.compile(find_text)
                stem = pattern.sub(
                    lambda m: power_replace(m, replace_text),
                    stem
                )
            except re.error as e:
                raise ValueError(
                    f"정규표현식 오류:\n{e}"
                ) from e
        else:
            stem = stem.replace(find_text, replace_text)

    return prefix + stem + suffix + ext



def create_plan(folder, find_text, replace_text,
                prefix, suffix, use_regex):

    files = get_files(folder)
    existing = os.listdir(folder)

    if use_regex and find_text:
        try:
            re.compile(find_text)
        except re.error as e:
            raise ValueError(
                f"정규표현식 오류:\n{e}"
            ) from e

    # 변경 대상 파일과 변경하지 않는 파일 구분
    candidates = []

    for old in files:
        new = make_target(
            old, find_text, replace_text,
            prefix, suffix, use_regex
        )
        validate_name(new)
        candidates.append((old, new))

    # 변경하지 않는 파일과 기존 폴더 이름은 보호
    protected = {
        name_key(name)
        for name in existing
        if name not in files
    }

    for old, new in candidates:
        if old == new:
            protected.add(name_key(old))

    used = set(protected)
    plan = []

    for old, desired in candidates:
        if old == desired:
            continue

        new = desired
        stem, ext = split_name(desired)
        number = 1

        # 중복 이름이 있으면 (1), (2), (3) 추가
        while name_key(new) in used:
            new = f"{stem} ({number}){ext}"
            validate_name(new)
            number += 1

        used.add(name_key(new))
        plan.append((old, new))

    # 기존 파일명과 충돌하면 임시 이름을
    # 거쳐 변경할 수 있는지 검사
    moving = {name_key(old) for old, _ in plan}

    for old, new in plan:
        for name in existing:
            if name_key(name) == name_key(new):
                if name_key(name) not in moving:
                    raise ValueError(
                        f"기존 파일과 충돌:\n{new}"
                    )

    return plan



def execute_plan(folder, plan):
    """
    임시 파일명을 이용한 2단계 변경.
    실패하면 가능한 범위에서 복구.
    """
    if not plan:
        return

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
        errors = []

        for old, temp, new in reversed(completed):
            try:
                os.rename(
                    os.path.join(folder, new),
                    os.path.join(folder, temp)
                )
            except OSError as e:
                errors.append(str(e))

        for old, temp, new in reversed(staged):
            try:
                if os.path.lexists(os.path.join(folder, temp)):
                    os.rename(
                        os.path.join(folder, temp),
                        os.path.join(folder, old)
                    )
            except OSError as e:
                errors.append(str(e))

        msg = f"파일명 변경 실패:\n{error}"

        if errors:
            msg += (
                "\n\n일부 복구 실패. 폴더를 확인하세요.\n"
                + "\n".join(errors[:5])
            )

        raise RuntimeError(msg) from error


class FileRenamerApp:

    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("950x680")
        self.root.minsize(750, 550)

        self.folder = tk.StringVar()
        self.find_text = tk.StringVar()
        self.replace_text = tk.StringVar()
        self.prefix = tk.StringVar()
        self.suffix = tk.StringVar()
        self.use_regex = tk.BooleanVar(value=True)

        self.undo_plan = []
        self.undo_folder = None

        self.build_ui()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text="한글 파일명 일괄 변환기",
            font=("Malgun Gothic", 18, "bold")
        ).pack(anchor="w", pady=(0, 12))

        folder_frame = ttk.LabelFrame(
            main, text="1. 작업 폴더", padding=10
        )
        folder_frame.pack(fill="x", pady=5)

        ttk.Entry(
            folder_frame,
            textvariable=self.folder
        ).pack(
            side="left", fill="x",
            expand=True, padx=(0, 10)
        )

        ttk.Button(
            folder_frame,
            text="폴더 선택",
            command=self.select_folder
        ).pack(side="right")

        rule_frame = ttk.LabelFrame(
            main, text="2. 변경 규칙", padding=10
        )
        rule_frame.pack(fill="x", pady=10)

        ttk.Checkbutton(
            rule_frame,
            text="정규표현식(RE) 사용 - $1, $2 그룹 치환 지원",
            variable=self.use_regex
        ).grid(
            row=0, column=0, columnspan=2,
            sticky="w", pady=8
        )

        rules = [
            ("찾을 문자", self.find_text),
            ("바꿀 문자", self.replace_text),
            ("앞에 추가", self.prefix),
            ("뒤에 추가", self.suffix),
        ]

        for i, (label, variable) in enumerate(rules, start=1):
            ttk.Label(
                rule_frame,
                text=label
            ).grid(
                row=i, column=0,
                sticky="w", pady=5
            )

            ttk.Entry(
                rule_frame,
                textvariable=variable,
                width=65
            ).grid(
                row=i, column=1,
                sticky="ew", padx=10, pady=5
            )

        rule_frame.columnconfigure(1, weight=1)

        ttk.Label(
            rule_frame,
            text=(
                "RE 예: (.*)_(\\d+)  →  $2_$1"
                "     |     $0=전체, $$=달러 문자"
            ),
            foreground="gray"
        ).grid(
            row=5, column=0, columnspan=2,
            sticky="w", pady=5
        )

        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=10)

        ttk.Button(
            buttons,
            text="미리보기",
            command=self.preview
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="파일명 변경 실행",
            command=self.rename_files
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="직전 변경 취소",
            command=self.undo
        ).pack(side="left", padx=5)

        preview_frame = ttk.LabelFrame(
            main, text="3. 변경 전후 미리보기", padding=10
        )
        preview_frame.pack(
            fill="both", expand=True, pady=5
        )

        self.tree = ttk.Treeview(
            preview_frame,
            columns=("old", "new"),
            show="headings"
        )

        self.tree.heading("old", text="변경 전")
        self.tree.heading("new", text="변경 후")

        self.tree.column("old", width=400)
        self.tree.column("new", width=400)

        scrollbar = ttk.Scrollbar(
            preview_frame,
            orient="vertical",
            command=self.tree.yview
        )

        self.tree.configure(
            yscrollcommand=scrollbar.set
        )

        self.tree.pack(
            side="left", fill="both", expand=True
        )
        scrollbar.pack(side="right", fill="y")

        self.status = tk.StringVar(
            value="작업 폴더를 선택하세요."
        )

        ttk.Label(
            main, textvariable=self.status
        ).pack(anchor="w", pady=8)

    def select_folder(self):
        folder = filedialog.askdirectory(
            title="파일이 있는 폴더 선택"
        )

        if folder:
            self.folder.set(folder)
            self.tree.delete(*self.tree.get_children())
            self.status.set("폴더 선택 완료")

    def get_plan(self):
        folder = self.folder.get()

        if not os.path.isdir(folder):
            raise ValueError("올바른 폴더를 선택하세요.")

        return create_plan(
            folder,
            self.find_text.get(),
            self.replace_text.get(),
            self.prefix.get(),
            self.suffix.get(),
            self.use_regex.get()
        )

    def show_plan(self, plan):
        self.tree.delete(*self.tree.get_children())

        for old, new in plan:
            self.tree.insert(
                "", "end", values=(old, new)
            )

        self.status.set(
            f"변경 대상: {len(plan)}개 파일"
        )

    def preview(self):
        try:
            plan = self.get_plan()
            self.show_plan(plan)
        except Exception as e:
            self.tree.delete(*self.tree.get_children())
            messagebox.showerror("미리보기 오류", str(e))

    def rename_files(self):
        try:
            plan = self.get_plan()
            self.show_plan(plan)

            if not plan:
                messagebox.showinfo(
                    "안내", "변경할 파일이 없습니다."
                )
                return

            if not messagebox.askyesno(
                "변경 확인",
                f"{len(plan)}개 파일명을 변경하시겠습니까?"
            ):
                return

            execute_plan(self.folder.get(), plan)

            self.undo_plan = [
                (new, old) for old, new in plan
            ]
            self.undo_folder = self.folder.get()

            self.status.set(
                f"변경 완료: {len(plan)}개"
            )

            messagebox.showinfo(
                "완료",
                f"{len(plan)}개 파일 변경 완료"
            )

        except Exception as e:
            messagebox.showerror("작업 오류", str(e))

    def undo(self):
        if not self.undo_plan:
            messagebox.showinfo(
                "안내", "취소할 작업이 없습니다."
            )
            return

        if not messagebox.askyesno(
            "변경 취소",
            "직전 변경 작업을 취소하시겠습니까?"
        ):
            return

        try:
            folder = self.undo_folder
            existing = os.listdir(folder)
            moving = {
                name_key(old)
                for old, new in self.undo_plan
            }

            for old, new in self.undo_plan:
                if not os.path.isfile(
                    os.path.join(folder, old)
                ):
                    raise ValueError(
                        f"복구 대상 파일 없음:\n{old}"
                    )

                for name in existing:
                    if name_key(name) == name_key(new):
                        if name_key(name) not in moving:
                            raise ValueError(
                                f"복구 이름 충돌:\n{new}"
                            )

            execute_plan(folder, self.undo_plan)

            count = len(self.undo_plan)
            self.undo_plan = []
            self.undo_folder = None

            self.tree.delete(*self.tree.get_children())
            self.status.set(
                f"변경 취소 완료: {count}개"
            )

            messagebox.showinfo(
                "완료", "파일명을 복구했습니다."
            )

        except Exception as e:
            messagebox.showerror("복구 오류", str(e))


if __name__ == "__main__":
    root = tk.Tk()
    app = FileRenamerApp(root)
    root.mainloop()
