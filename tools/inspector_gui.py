#!/usr/bin/env python3
"""Полевой маршрут: загрузка TIFF-слоёв инспектора в GeoServer (не повилика)."""
from __future__ import annotations

import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import inspector_cli as cli
import povilika_cli as pcli

SUPPORTED = (".tif", ".tiff")


def human_size(n: int) -> str:
    return f"{n / 2**30:.2f} ГБ" if n >= 2**30 else f"{n / 2**20:.0f} МБ"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__(className="FieldRouteUpload")
        self.title("Полевой маршрут — загрузка слоёв")
        icon = Path(__file__).with_name("assets") / "povilika_128.png"
        if icon.exists():
            self._icon = tk.PhotoImage(file=str(icon))
            self.iconphoto(True, self._icon)
        self.geometry("900x560")
        self.minsize(760, 480)
        self.env_file = cli.DEFAULT_ENV
        self.cfg = pcli.Config.from_env(self.env_file)
        self.rows: dict[str, dict] = {}
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.replace = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="")

        self.banner = ttk.Frame(self, padding=(10, 6))
        self.banner_text = ttk.Label(self.banner, foreground="#b71c1c")
        self.banner_text.pack(side="left")
        ttk.Button(self.banner, text="Запустить сервер", command=self.start_server).pack(side="right")

        nb = self.nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        nb.add(self._upload_tab(nb), text="  Загрузка TIFF  ")
        nb.add(self._published_tab(nb), text="  Опубликованные  ")
        nb.bind("<<NotebookTabChanged>>", lambda e: nb.index("current") == 1 and self.refresh_published())

        hint = ttk.Label(
            self,
            text="Слои для инспекторов (саранча / дельта Или). Снимки повилики сюда не загружаются.",
            foreground="#555",
        )
        hint.pack(fill="x", padx=12, pady=(0, 8))

        self.after(100, self._poll)
        self.after(200, self.check_server)

    def _upload_tab(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=8)
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Button(top, text="Добавить файлы…", command=self.pick_files).pack(side="left")
        ttk.Button(top, text="Убрать выбранные", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Checkbutton(top, text="Перезаписывать уже загруженные", variable=self.replace).pack(side="left", padx=12)

        cols = ("file", "size", "title", "status")
        self.table = ttk.Treeview(f, columns=cols, show="headings", selectmode="extended", height=12)
        for col, title, width, anchor in (
            ("file", "Файл", 360, "w"),
            ("size", "Размер", 90, "e"),
            ("title", "Название слоя", 280, "w"),
            ("status", "Статус", 140, "w"),
        ):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, anchor=anchor)
        self.table.pack(fill="both", expand=True, pady=(8, 0))
        self.table.bind("<Double-1>", self.edit_title)

        bottom = ttk.Frame(f)
        bottom.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status).pack(anchor="w", pady=4)
        self.start_btn = ttk.Button(bottom, text="Загрузить", command=self.start_upload)
        self.start_btn.pack(anchor="e", ipady=4)

        self.log = tk.Text(f, height=5, state="disabled", font=("Monospace", 9))
        self.log.pack(fill="x", pady=(8, 0))
        return f

    def _published_tab(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=8)
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Button(bar, text="Обновить", command=self.refresh_published).pack(side="left")
        ttk.Button(bar, text="Посмотреть на карте", command=self.preview).pack(side="left", padx=6)
        ttk.Button(bar, text="Снять с публикации", command=self.unpublish).pack(side="left")

        cols = ("name", "title")
        self.pub = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse", height=16)
        self.pub.heading("name", text="Слой")
        self.pub.heading("title", text="Название")
        self.pub.column("name", width=360)
        self.pub.column("title", width=400)
        self.pub.pack(fill="both", expand=True, pady=(8, 0))
        return f

    def write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def pick_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="TIFF слои для инспектора",
            filetypes=[("GeoTIFF", "*.tif *.tiff"), ("Все", "*.*")],
        )
        for f in files:
            self._add_row(Path(f))

    def _add_row(self, path: Path) -> None:
        if path.suffix.lower() not in SUPPORTED:
            return
        key = str(path.resolve())
        if key in self.rows:
            return
        store = cli._layer_name(path)
        title = cli.LAYER_TITLES.get(store, store)
        iid = self.table.insert("", "end", values=(path.name, human_size(path.stat().st_size), title, "в очереди"))
        self.rows[key] = {"path": path, "title": title, "iid": iid}

    def remove_selected(self) -> None:
        for iid in self.table.selection():
            for key, row in list(self.rows.items()):
                if row["iid"] == iid:
                    del self.rows[key]
            self.table.delete(iid)

    def edit_title(self, _event=None) -> None:
        sel = self.table.selection()
        if not sel:
            return
        iid = sel[0]
        row = next((r for r in self.rows.values() if r["iid"] == iid), None)
        if not row:
            return
        dlg = tk.Toplevel(self, class_="FieldRouteUpload")
        dlg.title("Название слоя")
        dlg.transient(self)
        var = tk.StringVar(value=row["title"])
        ttk.Label(dlg, text="Как будет видно в приложении:").pack(padx=12, pady=(12, 4))
        ttk.Entry(dlg, textvariable=var, width=50).pack(padx=12)
        def save():
            row["title"] = var.get().strip() or row["title"]
            self.table.set(iid, "title", row["title"])
            dlg.destroy()
        ttk.Button(dlg, text="Сохранить", command=save).pack(pady=12)

    def start_upload(self) -> None:
        if self.busy:
            return
        if not self.rows:
            messagebox.showinfo("Нечего загружать", "Добавьте файлы .tif")
            return
        self.busy = True
        self.start_btn.configure(state="disabled")
        todo = list(self.rows.values())
        replace = self.replace.get()

        def worker():
            ok = 0
            for i, row in enumerate(todo):
                iid, path, title = row["iid"], row["path"], row["title"]
                self.events.put(("status", iid, "копирование…"))
                def progress(done, total, iid=iid, i=i, n=len(todo)):
                    pct = done / total if total else 1
                    self.events.put(("progress", (i + pct) / n, f"{path.name}: {pct:.0%}"))
                    self.events.put(("status", iid, f"копирование {pct:.0%}"))
                try:
                    store = cli.publish_file(self.env_file, path, title=title, replace=replace, progress=progress)
                    self.events.put(("status", iid, "✔ опубликован"))
                    self.events.put(("log", f"✔ {path.name} → inspector_data:{store}"))
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    self.events.put(("status", iid, "✖ ошибка"))
                    self.events.put(("log", f"✖ {path.name}: {e}"))
            self.events.put(("finished", ok, len(todo)))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_published(self) -> None:
        for iid in self.pub.get_children():
            self.pub.delete(iid)
        try:
            for layer in cli.list_layers(self.env_file):
                self.pub.insert("", "end", iid=layer["name"], values=(layer["name"], layer["title"]))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Сервер", f"Не удалось получить список: {e}")

    def preview(self) -> None:
        sel = self.pub.selection()
        if not sel:
            messagebox.showinfo("Слой", "Выберите строку в списке")
            return
        name = sel[0]
        url = (f"{self.cfg.url}/{cli.WORKSPACE}/wms?service=WMS&version=1.1.0&request=GetMap"
               f"&layers={cli.WORKSPACE}:{name}&styles=&format=application/openlayers"
               f"&srs=EPSG:4326&width=800&height=500&bbox=70,44,78,47")
        webbrowser.open(url)

    def unpublish(self) -> None:
        sel = self.pub.selection()
        if not sel:
            return
        name = sel[0]
        if not messagebox.askyesno("Снять с публикации", f"Удалить слой {name} с сервера и из архива?"):
            return
        try:
            cli.remove_layer(self.env_file, name)
            self.refresh_published()
            self.write_log(f"[-] снят {name}")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ошибка", str(e))

    def check_server(self) -> None:
        try:
            ok = pcli.GeoServer(self.cfg).get("/about/version").status_code == 200
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            self.banner.pack_forget()
        else:
            self.banner_text.configure(text="Сервер GeoServer не отвечает.")
            self.banner.pack(fill="x", before=self.nb)

    def start_server(self) -> None:
        self.banner_text.configure(text="Запускаю сервер… (до 2 минут)")
        compose_dir = pcli.ROOT / "server"

        def run() -> None:
            # Один compose: PostGIS + GeoServer + FieldNav + шлюз
            subprocess.run(["docker", "compose", "up", "-d"], cwd=compose_dir, capture_output=True)
            for _ in range(40):
                try:
                    if pcli.GeoServer(self.cfg).get("/about/version").status_code == 200:
                        break
                except Exception:  # noqa: BLE001
                    pass
                threading.Event().wait(3)
            self.events.put(("check",))

        threading.Thread(target=run, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "status" and self.table.exists(ev[1]):
                    self.table.set(ev[1], "status", ev[2])
                elif kind == "progress":
                    if ev[1] is not None:
                        self.progress["value"] = ev[1]
                    self.status.set(ev[2])
                elif kind == "log":
                    self.write_log(ev[1])
                elif kind == "finished":
                    self.busy = False
                    self.start_btn.configure(state="normal")
                    self.progress["value"] = 1.0
                    self.status.set(f"Готово: {ev[1]} из {ev[2]}")
                    messagebox.showinfo("Загрузка", f"Опубликовано {ev[1]} из {ev[2]} файлов.")
                elif kind == "check":
                    self.check_server()
        except queue.Empty:
            pass
        self.after(100, self._poll)


if __name__ == "__main__":
    App().mainloop()
