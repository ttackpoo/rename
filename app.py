import re
import os
import uuid
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "한글 파일명 일괄 변환기"

def split_name(filename):
    """확장자는 유지하고 파일명 부분만 분리"""
    if filename.startswith(".") and filename.count(".") == 1:
        return filename, ""
    return os.path.splitext(filename)

def windows_key(name):
    """Windows의 대소문자 구분 없는 이름 비교"""
    return name.casefold()

def validate_name(name):
    if not name or name in (".", ".."):
        raise ValueError("빈 파일명은 사용할 수 없습니다.")

    forbidden = '<>:"/\\|?*'
    if any(c in forbidden or ord(c) < 32 for c in name):
        raise ValueError("Windows에서 사용할 수 없는 문자가 있습니다.")

    if name.endswith((" ", ".")):
        raise ValueError("파일명 끝에 공백이나 점을 사용할 수 없습니다.")

    base = name.split(".")[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved |= {f"COM{i}" for i in range(1, 10)}
    reserved |= {f"LPT{i}" for i in range(1, 10)}

    if base in reserved:
        raise ValueError("Windows 예약 파일명입니다.")

def get_files(folder):
    """현재 폴더의 일반 파일만 가져오기"""
    result = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False) and not entry.is_symlink():
                result.append(entry.name)
    return sorted(result, key=str.casefold)

def make_target(filename, find_text, replace_text, prefix, suffix):
    stem, ext = split_name(filename)

    if find_text:
        try:
            stem = re.sub(
                pattern=find_text,
                repl=replace_text,
                string=stem
            )
        except re.error as e:
            raise ValueError(
                f"정규표현식 오류:\n{e}"
            )

    return prefix + stem + suffix + ext

def create_plan(folder, find_text, replace_text, prefix, suffix):
    """파일명 변경 계획 및 충돌 검사"""
    files = get_files(folder)
    existing = set(os.listdir(folder))

    plan = []
    for old in files:
        new = make_target(
            old, find_text, replace_text, prefix, suffix
        )
        validate_name(new)
        if old != new:
            plan.append((old, new))

    # Windows 대소문자 구분 없이 검사
    targets = [windows_key(new) for _, new in plan]

    if len(targets) != len(set(targets)):
        raise ValueError("변경 결과에 중복 파일명이 있습니다.")

    # 변경 대상이 아닌 기존 파일/폴더와 충돌 검사
    moving = {windows_key(old) for old, _ in plan}

    for old, new in plan:
        for name in existing:
            if windows_key(name) == windows_key(new):
                if windows_key(name) not in moving:
                    raise ValueError(
                        f"기존 파일 또는 폴더와 충돌:\n{new}"
                    )

    return plan

def execute_plan(folder, plan):
    """
    임시 이름을 거치는 2단계 변경.
    오류가 발생하면 가능한 범위에서 원상 복구.
    """
    if not plan:
        return

    staged = []
    completed = []

    try:
        # 1단계: 원본 -> 고유 임시 이름
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
        rollback_errors = []

        # 완료된 파일을 다시 임시 이름으로 이동
        for old, temp, new in reversed(completed):
            try:
                os.rename(
                    os.path.join(folder, new),
                    os.path.join(folder, temp)
                )
            except OSError as e:
                rollback_errors.append(str(e))

        # 임시 이름에서 원본 이름으로 복구
        for old, temp, new in reversed(staged):
            try:
                if os.path.exists(os.path.join(folder, temp)):
                    os.rename(
                        os.path.join(folder, temp),
                        os.path.join(folder, old)
                    )
            except OSError as e:
                rollback_errors.append(str(e))

        msg = f"변경 작업 실패:\n{error}"
        if rollback_errors:
            msg += (
                "\n\n일부 파일 복구에 실패했습니다."
                "\n폴더의 파일명을 직접 확인하세요.\n"
                + "\n".join(rollback_errors[:5])
            )
        raise RuntimeError(msg) from error

class FileRenamerApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("900x650")
        self.root.minsize(700, 500)

        self.folder = tk.StringVar()
        self.find_text = tk.StringVar()
        self.replace_text = tk.StringVar()
        self.prefix = tk.StringVar()
        self.suffix = tk.StringVar()

        self.plan = []
        self.undo_plan = []
        self.undo_folder = None

        self.build_ui()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text=APP_TITLE,
            font=("Malgun Gothic", 17, "bold")
        ).pack(anchor="w", pady=(0, 15))

        folder_frame = ttk.LabelFrame(
            main, text="1. 작업 폴더 선택", padding=10
        )
        folder_frame.pack(fill="x", pady=5)

        ttk.Entry(
            folder_frame, textvariable=self.folder
        ).pack(side="left", fill="x", expand=True, padx=(0, 10))

        ttk.Button(
            folder_frame,
            text="폴더 선택",
            command=self.select_folder
        ).pack(side="right")

        rule_frame = ttk.LabelFrame(
            main, text="2. 파일명 변경 규칙", padding=10
        )
        rule_frame.pack(fill="x", pady=10)

        rules = [
            ("찾을 문자", self.find_text),
            ("바꿀 문자", self.replace_text),
            ("앞에 추가", self.prefix),
            ("뒤에 추가", self.suffix),
        ]

        for i, (label, variable) in enumerate(rules):
            ttk.Label(
                rule_frame, text=label
            ).grid(row=i, column=0, sticky="w", pady=5)

            ttk.Entry(
                rule_frame, textvariable=variable, width=55
            ).grid(
                row=i, column=1, sticky="ew",
                padx=10, pady=5
            )

        rule_frame.columnconfigure(1, weight=1)

        ttk.Label(
            rule_frame,
            text="※ 뒤에 추가하는 문자는 확장자(.jpg, .xlsx 등) 앞에 붙습니다.",
            foreground="gray"
        ).grid(row=4, column=0, columnspan=2, sticky="w")

        button_frame = ttk.Frame(main)
        button_frame.pack(fill="x", pady=10)

        ttk.Button(
            button_frame,
            text="미리보기",
            command=self.preview
        ).pack(side="left", padx=5)

        ttk.Button(
            button_frame,
            text="파일명 변경 실행",
            command=self.rename_files
        ).pack(side="left", padx=5)

        ttk.Button(
            button_frame,
            text="직전 변경 취소",
            command=self.undo
        ).pack(side="left", padx=5)

        preview_frame = ttk.LabelFrame(
            main, text="3. 변경 전후 미리보기", padding=10
        )
        preview_frame.pack(fill="both", expand=True, pady=5)

        columns = ("old", "new")
        self.tree = ttk.Treeview(
            preview_frame,
            columns=columns,
            show="headings"
        )
        self.tree.heading("old", text="변경 전 파일명")
        self.tree.heading("new", text="변경 후 파일명")
        self.tree.column("old", width=350)
        self.tree.column("new", width=350)

        scrollbar = ttk.Scrollbar(
            preview_frame,
            orient="vertical",
            command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.status = tk.StringVar(value="작업 폴더를 선택하세요.")
        ttk.Label(
            main,
            textvariable=self.status
        ).pack(anchor="w", pady=8)

    def select_folder(self):
        folder = filedialog.askdirectory(
            title="파일이 있는 폴더 선택"
        )
        if folder:
            self.folder.set(folder)
            self.plan = []
            self.tree.delete(*self.tree.get_children())
            self.status.set("폴더가 선택되었습니다.")

    def preview(self):
        folder = self.folder.get()

        if not os.path.isdir(folder):
            messagebox.showwarning(
                "알림", "올바른 폴더를 선택하세요."
            )
            return

        self.tree.delete(*self.tree.get_children())
        self.plan = []

        try:
            plan = create_plan(
                folder,
                self.find_text.get(),
                self.replace_text.get(),
                self.prefix.get(),
                self.suffix.get()
            )

            for old, new in plan:
                self.tree.insert(
                    "", "end", values=(old, new)
                )

            self.plan = plan
            self.status.set(
                f"미리보기 완료: 변경 대상 {len(plan)}개"
            )

        except Exception as e:
            messagebox.showerror("오류", str(e))

    def rename_files(self):
        folder = self.folder.get()

        if not os.path.isdir(folder):
            messagebox.showwarning(
                "알림", "올바른 폴더를 선택하세요."
            )
            return

        # 실행 직전에 최신 파일 목록으로 다시 검사
        try:
            plan = create_plan(
                folder,
                self.find_text.get(),
                self.replace_text.get(),
                self.prefix.get(),
                self.suffix.get()
            )
        except Exception as e:
            messagebox.showerror("오류", str(e))
            return

        if not plan:
            messagebox.showinfo(
                "안내", "변경할 파일이 없습니다."
            )
            return

        self.tree.delete(*self.tree.get_children())
        for old, new in plan:
            self.tree.insert("", "end", values=(old, new))

        if not messagebox.askyesno(
            "변경 확인",
            f"{len(plan)}개 파일의 이름을 변경하시겠습니까?"
        ):
            return

        try:
            execute_plan(folder, plan)

            # 역방향 계획 저장: 최종 -> 원본
            self.undo_plan = [
                (new, old) for old, new in plan
            ]
            self.undo_folder = folder

            self.status.set(
                f"변경 완료: {len(plan)}개 파일"
            )
            messagebox.showinfo(
                "완료",
                f"{len(plan)}개 파일의 이름을 변경했습니다."
            )

        except Exception as e:
            self.undo_plan = []
            self.undo_folder = None
            messagebox.showerror("작업 오류", str(e))

    def undo(self):
        if not self.undo_plan or not self.undo_folder:
            messagebox.showinfo(
                "안내", "취소할 변경 기록이 없습니다."
            )
            return

        if not messagebox.askyesno(
            "변경 취소",
            "직전 파일명 변경을 취소하시겠습니까?"
        ):
            return

        try:
            folder = self.undo_folder

            existing = os.listdir(folder)
            moving = {
                windows_key(old)
                for old, new in self.undo_plan
            }

            # 복구 대상이 그대로 존재하는지 확인
            for old, new in self.undo_plan:
                if not os.path.isfile(
                    os.path.join(folder, old)
                ):
                    raise ValueError(
                        f"변경된 파일이 없습니다:\n{old}"
                    )

                for name in existing:
                    if windows_key(name) == windows_key(new):
                        if windows_key(name) not in moving:
                            raise ValueError(
                                f"복구할 이름이 이미 존재합니다:\n{new}"
                            )

            execute_plan(folder, self.undo_plan)

            count = len(self.undo_plan)
            self.undo_plan = []
            self.undo_folder = None
            self.plan = []

            self.tree.delete(*self.tree.get_children())
            self.status.set(
                f"변경 취소 완료: {count}개 파일"
            )

            messagebox.showinfo(
                "완료", "파일명을 원래대로 복구했습니다."
            )

        except Exception as e:
            messagebox.showerror("복구 오류", str(e))

if __name__ == "__main__":
    root = tk.Tk()
    app = FileRenamerApp(root)
    root.mainloop()
