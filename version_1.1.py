import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import os
import io
import pymupdf
from PIL import Image, ImageTk
import sys

try:
    RESAMPLE_FILTER = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_FILTER = Image.LANCZOS


def fix_image_exif(img):
    """校正图片EXIF方向，和系统图片查看器方向保持一致"""
    try:
        exif = img._getexif()
        if exif is not None:
            orientation = exif.get(0x0112)
            rotate_map = {
                2: Image.FLIP_LEFT_RIGHT,
                3: Image.ROTATE_180,
                4: Image.FLIP_TOP_BOTTOM,
                5: Image.TRANSPOSE,
                6: Image.ROTATE_270,
                7: Image.TRANSVERSE,
                8: Image.ROTATE_90,
            }
            if orientation in rotate_map:
                img = img.transpose(rotate_map[orientation])
    except Exception:
        pass
    return img


def global_excepthook(exc_type, exc_value, exc_traceback):
    """全局未捕获异常钩子，弹窗提示，防止无声闪退"""
    import traceback
    err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    messagebox.showerror("程序异常", f"发生未捕获异常：\n{err_msg}")


class SimplePdfApp:
    def __init__(self, root):
        sys.excepthook = global_excepthook
        self.root = root
        self.root.title("PDF处理工具")
        self.root.geometry("1080x620")

        self.selected_files = []
        self.source_doc = pymupdf.open()
        self.raw_pages = []
        self.raw_sub_docs = []
        self.page_thumbnails = []
        self.thumb_frames = []

        self.page_indices = []
        self.selected_display_idx = None
        self.right_click_disp_idx = None  # 记录右键点击的页面索引
        self._pending_select_index = None  # 异步渲染完成后需要高亮的页面索引

        self.standard_thumb_width = 180
        self.max_thumb_width = 240
        self.card_margin = 12

        self.default_base_w = 595.0
        self.current_base_w = self.default_base_w
        self.PAGE_DRAW_MARGIN_PT = 12  # 页面重绘时四周最小边距，等比例不变形

        # ============【PDF压缩配置】============
        self.var_enable_compress = tk.BooleanVar(value=False)
        self.compress_dpi_list = [72, 100, 150, 200, 300]
        self.var_compress_dpi = tk.StringVar(value="150")
        self.jpeg_quality_list = [40, 50, 60, 70, 80, 90]
        self.var_jpeg_quality = tk.StringVar(value="70")

        top_frame = ttk.Frame(root)
        top_frame.pack(pady=10)

        self.btn_add = ttk.Button(top_frame, text="添加文件", command=self.add_file)
        self.btn_add.grid(row=0, column=0, padx=4)

        self.btn_merge = ttk.Button(top_frame, text="合并文件", command=self.on_merge)
        self.btn_merge.grid(row=0, column=1, padx=4)

        self.btn_back_list = ttk.Button(top_frame, text="返回源文件列表", command=self.go_back_file_list, state="disabled")
        self.btn_back_list.grid(row=0, column=2, padx=4)

        self.btn_del_page = ttk.Button(top_frame, text="删除选中页", command=self.delete_selected_page, state="disabled")
        self.btn_del_page.grid(row=0, column=3, padx=4)

        self.btn_save = ttk.Button(top_frame, text="保存PDF", command=self.on_save, state="disabled")
        self.btn_save.grid(row=0, column=4, padx=4)

        ttk.Label(top_frame, text="页面宽度(pt):").grid(row=0, column=5, padx=(10, 2))
        self.var_base_w = tk.StringVar(value=f"{self.current_base_w:.1f}")
        self.entry_width = ttk.Entry(top_frame, textvariable=self.var_base_w, width=8)
        self.entry_width.grid(row=0, column=6, padx=2)
        self.btn_apply_width = ttk.Button(top_frame, text="重新应用尺寸", command=self.apply_new_width, state="disabled")
        self.btn_apply_width.grid(row=0, column=7, padx=4)

        # ========压缩UI（第二行工具栏）========
        compress_frame = ttk.Frame(root)
        compress_frame.pack(pady=(0,6), padx=15, anchor="w")
        self.chk_compress = ttk.Checkbutton(compress_frame, text="导出时压缩PDF", variable=self.var_enable_compress)
        self.chk_compress.grid(row=0, column=0, padx=(0,8))

        ttk.Label(compress_frame, text="图像DPI:").grid(row=0, column=1, padx=(0,4))
        self.cmb_dpi = ttk.Combobox(compress_frame, textvariable=self.var_compress_dpi,
                                     values=[str(x) for x in self.compress_dpi_list], width=6, state="readonly")
        self.cmb_dpi.grid(row=0, column=2)
        self.cmb_dpi.set("150")

        ttk.Label(compress_frame, text="JPEG质量:").grid(row=0, column=3, padx=(12,4))
        self.cmb_quality = ttk.Combobox(compress_frame, textvariable=self.var_jpeg_quality,
                                        values=[str(x) for x in self.jpeg_quality_list], width=6, state="readonly")
        self.cmb_quality.grid(row=0, column=4)
        self.cmb_quality.set("70")

        hint_text = ("提示：压缩仅对图片生效；纯文字PDF几乎不会变小；"
                     "DPI、质量数值越小文件越小，图片清晰度下降；不勾选输出原始质量。")
        ttk.Label(compress_frame, text=hint_text, foreground="#555555").grid(row=0, column=5, padx=(12,0))

        info_text = ("缩略图：左键单击选中页面；【右击缩略图】弹出菜单：删除本页 / 移动到指定页码；"
                     "输入N，移动后严格为第N页；缩略图自动裁掉四周空白仅为预览；导出PDF完整保留设置的纸张尺寸。")
        self.label_info = ttk.Label(root, text=info_text)
        self.label_info.pack(anchor="w", padx=15)

        self.main_container = ttk.Frame(root)
        self.main_container.pack(padx=15, pady=5, fill=tk.BOTH, expand=True)

        # 源文件列表视图
        self.list_container = ttk.Frame(self.main_container)
        self.list_container.pack(fill=tk.BOTH, expand=True)

        self.file_listbox = tk.Listbox(self.list_container, selectmode=tk.SINGLE)
        self.file_listbox.pack(side="left", fill=tk.BOTH, expand=True)

        scrollbar_list = tk.Scrollbar(self.list_container, orient="vertical", command=self.file_listbox.yview, width=12)
        scrollbar_list.pack(side="right", fill="y")
        self.file_listbox.config(yscrollcommand=scrollbar_list.set)

        # 缩略图视图
        self.thumb_container = ttk.Frame(self.main_container)
        self.canvas = tk.Canvas(self.thumb_container)
        self.thumb_scroll = tk.Scrollbar(self.thumb_container, orient="vertical", command=self.canvas.yview, width=16)
        self.thumb_inner = tk.Frame(self.canvas)

        self.canvas_window_id = self.canvas.create_window((0, 0), window=self.thumb_inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.thumb_scroll.set)

        self.canvas.pack(side="left", fill=tk.BOTH, expand=True)
        self.thumb_scroll.pack(side="right", fill="y")

        self.canvas.bind("<Configure>", self.on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

        # 源文件listbox拖拽
        self.file_drag_index = None
        self.file_listbox.bind("<ButtonPress-1>", self.file_drag_start)
        self.file_listbox.bind("<B1-Motion>", self.file_drag_motion)
        self.file_listbox.bind("<ButtonRelease-1>", self.file_drag_end)

        # 缩略图右键菜单
        self.right_click_menu = tk.Menu(root, tearoff=0)
        self.right_click_menu.add_command(label="删除本页面", command=self._menu_delete_current_page)
        self.right_click_menu.add_command(label="移动到指定页码...", command=self._menu_move_to_target_pos)

        # 源文件列表右键菜单
        self.right_menu = tk.Menu(root, tearoff=0)
        self.right_menu.add_command(label="删除该项", command=self.delete_selected_item)
        self.file_listbox.bind("<Button-3>", self.show_right_menu)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._skip_resize = False

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta // 120)), "units")

    def _forward_wheel(self, event):
        self.canvas.event_generate("<MouseWheel>", delta=event.delta)
        return "break"

    def bind_wheel_to_subtree(self, widget):
        widget.bind("<MouseWheel>", self._forward_wheel)
        for child in widget.winfo_children():
            self.bind_wheel_to_subtree(child)

    # ======================【右键菜单业务逻辑｜全部异步，杜绝闪退｜移动零偏移重排算法】======================
    def _show_thumb_context_menu(self, event, disp_idx):
        """缩略图卡片右键触发：仅记录索引，弹出菜单；不做任何销毁/重绘"""
        self.right_click_disp_idx = disp_idx
        self.selected_display_idx = disp_idx
        self.refresh_highlight()
        self.right_click_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _menu_delete_current_page(self):
        """右键菜单：删除当前右键点击的页面；只修改内存，异步执行UI重建"""
        disp_idx = self.right_click_disp_idx
        if disp_idx is None:
            self.right_click_disp_idx = None
            return
        total = len(self.page_indices)
        if total <= 1:
            messagebox.showwarning("无法删除", "文档至少需要保留1个页面，不能全部删除！")
            self.right_click_disp_idx = None
            return
        ok = messagebox.askyesno("确认删除", f"确定删除第 {disp_idx+1} 页？\n删除后可以重新合并源文件恢复。")
        if not ok:
            self.right_click_disp_idx = None
            return

        del self.page_indices[disp_idx]
        self._pending_select_index = None
        self.right_click_disp_idx = None
        self.root.after(1, self._async_apply_page_reorder)

    def _menu_move_to_target_pos(self):
        """
        右键菜单：移动到指定页码
        ✅零限制重排算法：输入N，移动完成后页面严格就是第N页；不分向前向后，无可达限制；
        ✅仅修改内存列表；PDF重建、UI重绘全部异步调度，规避右键事件内destroy控件闪退。
        """
        src_disp_idx = self.right_click_disp_idx
        if src_disp_idx is None:
            self.right_click_disp_idx = None
            return

        orig_total = len(self.page_indices)
        if orig_total <= 1:
            messagebox.showinfo("提示", "只有1个页面，无需移动。")
            self.right_click_disp_idx = None
            return

        src_ui_page = src_disp_idx + 1
        prompt_text = (
            f"当前页面：第{src_ui_page}页\n"
            f"总页面数量：{orig_total}页\n"
            f"请输入【移动完成后】目标页码（范围：1 ~ {orig_total}）\n"
            f"输入第几页，移动结束该页面就严格落在第几页，无位置限制。"
        )
        ans_str = simpledialog.askstring("移动页面到指定位置", prompt_text)
        if ans_str is None:
            self.right_click_disp_idx = None
            return
        ans_str = ans_str.strip()
        if not ans_str.isdigit():
            messagebox.showerror("输入错误", "请输入有效的数字！")
            self.right_click_disp_idx = None
            return

        dst_ui_page = int(ans_str)
        if not (1 <= dst_ui_page <= orig_total):
            messagebox.showerror("输入错误", f"页码超出范围！合法范围：1 ~ {orig_total}")
            self.right_click_disp_idx = None
            return

        src_idx = src_disp_idx
        desired_final_dst = dst_ui_page - 1

        if src_idx == desired_final_dst:
            messagebox.showinfo("提示", "目标位置和当前位置相同，无需移动。")
            self.right_click_disp_idx = None
            return

        # 零限制重排：新建列表拷贝，彻底消除pop+insert偏移BUG
        move_item = self.page_indices[src_idx]
        new_page_list = []
        for idx, item in enumerate(self.page_indices):
            if idx != src_idx:
                new_page_list.append(item)
        new_page_list.insert(desired_final_dst, move_item)
        self.page_indices[:] = new_page_list

        self._pending_select_index = desired_final_dst
        self.right_click_disp_idx = None
        self.root.after(1, self._async_apply_page_reorder)

    def _async_apply_page_reorder(self):
        """异步执行：页面顺序已经在内存改完；这里才执行PDF重建 + 缩略图重绘；隔离鼠标事件栈，防止闪退"""
        try:
            self._build_preview_from_raw(self.current_base_w)
            self.render_thumbnails()
            if hasattr(self, "_pending_select_index") and self._pending_select_index is not None:
                idx = self._pending_select_index
                if 0 <= idx < len(self.page_indices):
                    self.selected_display_idx = idx
                else:
                    self.selected_display_idx = None
                self.refresh_highlight()
            finish_msg = "页面操作完成"
            if self._pending_select_index is not None and 0 <= self._pending_select_index < len(self.page_indices):
                finish_msg = f"页面操作完成，当前页面为第{self._pending_select_index+1}页"
            self._pending_select_index = None
            messagebox.showinfo("完成", finish_msg)
        except Exception as e:
            self._pending_select_index = None
            messagebox.showerror("页面操作异常", f"处理失败：{str(e)}")

    def bind_thumb_card_events(self, frm, disp_idx):
        """绑定卡片：左键=选中；右键=弹出上下文菜单；子控件全部透传事件"""
        def left_click_cb(ev, d=disp_idx):
            self.on_thumb_click(d)
            return "break"

        def right_click_cb(ev, d=disp_idx):
            self._show_thumb_context_menu(ev, d)
            return "break"

        frm.bind("<ButtonPress-1>", left_click_cb)
        frm.bind("<Button-3>", right_click_cb)
        for child in frm.winfo_children():
            child.bind("<ButtonPress-1>", left_click_cb)
            child.bind("<Button-3>", right_click_cb)
    # =================================================================

    def on_close(self):
        try:
            self.source_doc.close()
        except Exception:
            pass
        for d in self.raw_sub_docs:
            try:
                d.close()
            except Exception:
                pass
        self.root.destroy()

    def go_back_file_list(self):
        self.right_click_disp_idx = None
        self._pending_select_index = None
        try:
            self.source_doc.close()
        except Exception:
            pass
        self.source_doc = pymupdf.open()
        for d in self.raw_sub_docs:
            try:
                d.close()
            except Exception:
                pass
        self.raw_sub_docs.clear()
        self.raw_pages.clear()
        self.page_indices.clear()
        self.selected_display_idx = None
        for w in self.thumb_inner.winfo_children():
            w.destroy()
        self.page_thumbnails.clear()
        self.thumb_frames.clear()
        self.current_base_w = self.default_base_w
        self.var_base_w.set(f"{self.default_base_w:.1f}")
        self.show_file_list_view()

    def show_file_list_view(self):
        self.thumb_container.pack_forget()
        self.list_container.pack(fill=tk.BOTH, expand=True)
        self.label_info.config(text="已选中文档列表，拖动调整源文件顺序；添加文件后点击【合并文件】生成预览")
        self.btn_del_page.config(state="disabled")
        self.btn_save.config(state="disabled")
        self.btn_apply_width.config(state="disabled")
        self.btn_back_list.config(state="disabled")

    def show_thumbnail_view(self):
        self.list_container.pack_forget()
        self.thumb_container.pack(fill=tk.BOTH, expand=True)
        info_text = ("缩略预览：左键单击选中页面；【右击缩略图】弹出菜单：删除本页 / 移动到指定页码；"
                     "输入N移动后严格为第N页；导出PDF完整保留设置的纸张尺寸；滚轮滚动；可改宽度重渲染；可返回源文件列表")
        self.label_info.config(text=info_text)
        self.btn_del_page.config(state="normal")
        self.btn_save.config(state="normal")
        self.btn_apply_width.config(state="normal")
        self.btn_back_list.config(state="normal")
        w = self.canvas.winfo_width()
        self.canvas.itemconfig(self.canvas_window_id, width=w)
        self.render_thumbnails()

    def on_canvas_resize(self, event):
        if self._skip_resize:
            return
        if not self.page_indices:
            return
        self.canvas.itemconfig(self.canvas_window_id, width=event.width)
        self._skip_resize = True
        self.render_thumbnails()
        self.root.after(80, lambda: setattr(self, "_skip_resize", False))

    def refresh_listbox(self):
        scroll_pos = self.file_listbox.yview()
        sel = self.file_listbox.curselection()
        self.file_listbox.delete(0, tk.END)
        for path in self.selected_files:
            self.file_listbox.insert(tk.END, os.path.basename(path))
        self.file_listbox.yview_moveto(scroll_pos[0])
        if sel:
            self.file_listbox.select_set(sel[0])

    def add_file(self):
        paths = filedialog.askopenfilenames(
            title="选择PDF或者图片",
            filetypes=[
                ("支持文件", "*.pdf;*.jpg;*.jpeg;*.png;*.bmp;*.tiff"),
                ("PDF文件", "*.pdf"),
                ("图片", "*.jpg;*.jpeg;*.png;*.bmp;*.tiff")
            ]
        )
        for p in paths:
            if p not in self.selected_files:
                self.selected_files.append(p)
        self.refresh_listbox()

    # =====================【修复后的图片导入函数：完整兼容PNG‑P/PNG‑L/RGBA/RGB】=====================
    def _image_to_raw_pdf_page(self, img_path):
        tmp_doc = None
        try:
            tmp_doc = pymupdf.open()
            im = Image.open(img_path)
            im = fix_image_exif(im)

            # P=调色板PNG；L=灰度图；全部转为RGBA统一处理
            if im.mode in ("P", "L"):
                im = im.convert("RGBA")

            w, h = im.size

            # RGBA透明图合成白色背景
            if im.mode == "RGBA":
                background = Image.new("RGB", im.size, (255, 255, 255))
                background.paste(im, mask=im.split()[3])
                im = background
            else:
                # RGB模式强制转换，兼容各类异常图片模式
                im = im.convert("RGB")

            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=95, optimize=True)
            buf.seek(0)

            tmp_doc.new_page(width=w, height=h)
            tmp_doc[0].insert_image(pymupdf.Rect(0, 0, w, h), stream=buf.getvalue())
            buf.close()
            return tmp_doc, tmp_doc[0]
        except Exception as e:
            if tmp_doc is not None:
                try:
                    tmp_doc.close()
                except Exception:
                    pass
            messagebox.showerror("图片解析失败", f"文件：{os.path.basename(img_path)}\n错误信息：{str(e)}")
            return None, None

    def _scale_raw_page_to_target_width(self, raw_page, base_w):
        """
        ✅修复版本：等比例不变形绘制，四周仅保留固定小边距；
        不会将源内容缩小到大页面正中间产生大片空白；
        新建页面物理纸张宽度=base_w，高度跟随源页面宽高比。
        """
        if raw_page is None:
            raise ValueError("raw_page 不能为None")
        src_rect = raw_page.rect
        src_w = src_rect.width
        src_h = raw_page.rect.height

        scale = base_w / src_w
        out_w = base_w
        out_h = src_h * scale
        new_page = self.source_doc.new_page(width=out_w, height=out_h)

        margin_pt = self.PAGE_DRAW_MARGIN_PT
        avail_w = out_w - 2 * margin_pt
        avail_h = out_h - 2 * margin_pt
        src_aspect = src_w / src_h
        avail_aspect = avail_w / avail_h

        if src_aspect > avail_aspect:
            draw_w = avail_w
            draw_h = draw_w / src_aspect
        else:
            draw_h = avail_h
            draw_w = draw_h * src_aspect

        x0 = margin_pt + (avail_w - draw_w) / 2.0
        y0 = margin_pt + (avail_h - draw_h) / 2.0
        dest_rect = pymupdf.Rect(x0, y0, x0 + draw_w, y0 + draw_h)
        new_page.show_pdf_page(dest_rect, raw_page.parent, raw_page.number, keep_proportion=True)
        return new_page

    def _build_preview_from_raw(self, base_w):
        """
        ⚠️重大BUG修复：就地过滤无效索引，**绝不直接覆盖self.page_indices，保护用户手动调整的页面顺序**
        """
        try:
            self.source_doc.close()
        except Exception:
            pass
        self.source_doc = pymupdf.open()

        keep_indices = []
        for raw_idx in self.page_indices:
            raw_p = self.raw_pages[raw_idx]
            if raw_p is None:
                continue
            try:
                self._scale_raw_page_to_target_width(raw_p, base_w)
                keep_indices.append(raw_idx)
            except Exception:
                continue
        self.page_indices[:] = keep_indices
        self.current_base_w = base_w
        self.var_base_w.set(f"{base_w:.1f}")

    def apply_new_width(self):
        if not self.raw_pages or not self.page_indices:
            messagebox.showwarning("提示", "请先合并文件后再重设尺寸")
            return
        try:
            new_w = float(self.var_base_w.get())
        except ValueError:
            messagebox.showerror("输入错误", "请输入有效的数字，单位pt，例如595")
            return
        if new_w <= 10 or new_w > 5000:
            messagebox.showerror("范围错误", "宽度需要在10 ~ 5000 pt之间")
            return
        try:
            self._build_preview_from_raw(new_w)
            self.selected_display_idx = None
            self.render_thumbnails()
            messagebox.showinfo("完成", f"页面宽度已更新为 {new_w:.1f} pt")
        except Exception as e:
            messagebox.showerror("重渲染失败", str(e))

    def on_merge(self):
        if not self.selected_files:
            messagebox.showwarning("提示", "请先添加文件")
            return
        self.raw_pages.clear()
        for d in self.raw_sub_docs:
            try:
                d.close()
            except Exception:
                pass
        self.raw_sub_docs.clear()
        self.right_click_disp_idx = None
        self._pending_select_index = None

        try:
            for fpath in self.selected_files:
                ext = os.path.splitext(fpath)[1].lower()
                if ext == ".pdf":
                    sub = pymupdf.open(fpath)
                    for page in sub:
                        page.remove_rotation()
                        raw_sub = pymupdf.open()
                        raw_sub.insert_pdf(sub, from_page=page.number, to_page=page.number)
                        self.raw_sub_docs.append(raw_sub)
                        if len(raw_sub) >= 1:
                            raw_p = raw_sub[0]
                            if raw_p is not None:
                                self.raw_pages.append(raw_p)
                    sub.close()
                else:
                    # 图片格式（修复PNG解析失败容错）
                    tmp_doc, raw_img_page = self._image_to_raw_pdf_page(fpath)
                    if tmp_doc is not None:
                        self.raw_sub_docs.append(tmp_doc)
                    if raw_img_page is not None:
                        self.raw_pages.append(raw_img_page)
                    else:
                        # 图片解析失败，关闭临时文档，不加入页面列表
                        try:
                            if tmp_doc is not None:
                                tmp_doc.close()
                        except Exception:
                            pass

            if len(self.raw_pages) == 0:
                messagebox.showerror("合并失败", "没有读到任何有效页面，文件可能损坏/加密，或图片解析全部失败。")
                return

            self.page_indices = list(range(len(self.raw_pages)))
            self._build_preview_from_raw(self.default_base_w)
            self.selected_display_idx = None
            self.show_thumbnail_view()

        except Exception as e:
            messagebox.showerror("合并失败", str(e))
        return

    def render_thumbnails(self):
        """
        ✅缩略图预览修复：
        GUI预览使用页面内容bbox裁剪四周大片空白，仅用于界面显示；
        【导出PDF完全不使用该裁剪，完整保留物理纸张尺寸】
        """
        for w in self.thumb_inner.winfo_children():
            w.destroy()
        self.page_thumbnails.clear()
        self.thumb_frames.clear()

        container_w = self.canvas.winfo_width()
        if container_w < 200:
            container_w = 200
        card_total_width = self.standard_thumb_width + self.card_margin
        cols = max(1, container_w // card_total_width)

        for disp_idx, src_page_no in enumerate(self.page_indices):
            page = self.source_doc[disp_idx]

            # 获取页面实际图文包围盒，用于缩略图预览裁剪空白；导出PDF完全不受此影响
            try:
                content_bbox = page.get_bbox()
                if content_bbox and (content_bbox.width > 1 and content_bbox.height > 1):
                    render_clip = content_bbox
                else:
                    render_clip = page.rect
            except Exception:
                render_clip = page.rect

            pix = page.get_pixmap(clip=render_clip)
            pil_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            orig_w, orig_h = pil_img.size
            pw = render_clip.width
            scale_factor = pw / 595
            tw = int(self.standard_thumb_width * scale_factor)
            tw = min(tw, self.max_thumb_width)
            th = int(orig_h * (tw / orig_w))
            pil_img = pil_img.resize((tw, th), RESAMPLE_FILTER)
            tk_img = ImageTk.PhotoImage(pil_img)
            self.page_thumbnails.append(tk_img)

            frm = tk.Frame(self.thumb_inner, bg="#f6f6f6", highlightthickness=2, highlightbackground="#cccccc")
            row = disp_idx // cols
            col = disp_idx % cols
            frm.grid(row=row, column=col, padx=6, pady=8, sticky="nw")
            frm.disp_index = disp_idx
            frm.src_page = src_page_no
            self.thumb_frames.append(frm)

            lbl_img = tk.Label(frm, image=tk_img, bg="#f6f6f6")
            lbl_img.pack(padx=4, pady=3)
            info_lbl = tk.Label(frm, text=f"第{disp_idx+1}页", bg="#f6f6f6")
            info_lbl.pack(padx=4)

            self.bind_thumb_card_events(frm, disp_idx)

        self.bind_wheel_to_subtree(self.thumb_inner)
        self.thumb_inner.update_idletasks()
        try:
            self.canvas.config(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            pass
        self.refresh_highlight()

    def refresh_highlight(self):
        for idx, frm in enumerate(self.thumb_frames):
            try:
                if idx == self.selected_display_idx:
                    frm.config(highlightbackground="#0055bb", bg="#cce0ff", highlightthickness=3)
                    for child in frm.winfo_children():
                        child.config(bg="#cce0ff")
                else:
                    frm.config(highlightbackground="#cccccc", bg="#f6f6f6", highlightthickness=2)
                    for child in frm.winfo_children():
                        child.config(bg="#f6f6f6")
            except tk.TclError:
                continue

    def on_thumb_click(self, disp_idx):
        self.selected_display_idx = disp_idx
        self.refresh_highlight()

    def delete_selected_page(self):
        """顶部按钮：删除左键选中的页面；改为异步渲染，规避同类崩溃风险"""
        if self.selected_display_idx is None:
            messagebox.showinfo("提示", "请点击页面卡片任意位置选中（蓝色背景+粗蓝边框）")
            return
        if len(self.page_indices) <= 1:
            messagebox.showwarning("警告", "文档至少保留1页，不能删除！")
            return
        ok = messagebox.askyesno("确认删除", f"确定删除选中的第 {self.selected_display_idx+1} 页？")
        if not ok:
            return
        del self.page_indices[self.selected_display_idx]
        self._pending_select_index = None
        self.selected_display_idx = None
        self.root.after(1, self._async_apply_page_reorder)

    # =====================【PDF保存 + 官方rewrite_images压缩实现】=====================
    def on_save(self):
        if len(self.page_indices) == 0:
            return
        save_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF 文件", "*.pdf")],
            title="保存编辑完成的PDF"
        )
        if not save_path:
            return

        enable_compress = self.var_enable_compress.get()
        try:
            target_dpi = int(self.var_compress_dpi.get())
            jpeg_quality = int(self.var_jpeg_quality.get())
        except ValueError:
            target_dpi = 150
            jpeg_quality = 70

        # 建立输出文档副本，绝不修改内存预览self.source_doc，预览永远高清
        out_doc = pymupdf.open()
        try:
            # 完整拷贝全部页面，物理纸张尺寸完整保留
            for src_no in range(len(self.source_doc)):
                out_doc.insert_pdf(self.source_doc, from_page=src_no, to_page=src_no)

            original_size = 0
            if enable_compress:
                messagebox.showinfo("压缩提示",
                                    f"即将执行图像压缩：\n目标DPI={target_dpi}，JPEG质量={jpeg_quality}\n"
                                    "仅位图/照片会被降采样；纯文字PDF体积几乎无变化；大文档会消耗数秒，请等待。")
                self.root.update_idletasks()
                temp_raw = io.BytesIO()
                out_doc.save(temp_raw, garbage=4, deflate=True)
                original_size = len(temp_raw.getvalue())
                temp_raw.close()

                try:
                    # PyMuPDF官方API重写全部图片，清理旧图像对象
                    out_doc.rewrite_images(
                        dpi_threshold=target_dpi + 10,
                        dpi_target=target_dpi,
                        quality=jpeg_quality,
                        lossy=True,
                        lossless=True,
                        bitonal=True,
                        color=True,
                        gray=True,
                        set_to_gray=False
                    )
                    out_doc.save(save_path, garbage=4, deflate=True)
                except Exception as e:
                    messagebox.showwarning("图像压缩发生异常",
                                           f"图片重编码失败，已回退原始画质保存。\n原因：{str(e)}\n"
                                           "提示：部分PDF存在跨页共享图片(每页水印)会触发该问题。")
                    out_doc.save(save_path, garbage=4, deflate=True)
            else:
                out_doc.save(save_path, garbage=4, deflate=True)

            final_size = os.path.getsize(save_path)
            msg = f"保存完成！\n文件路径：{save_path}"
            if enable_compress and original_size > 0:
                reduce_ratio = (1.0 - final_size / original_size) * 100
                msg += f"\n【压缩统计】压缩前:{original_size / 1024:.1f} KB，压缩后:{final_size / 1024:.1f} KB\n体积减少 {reduce_ratio:.1f}%"
            msg += f"\n当前页面基准宽度：{self.current_base_w:.1f}pt"
            messagebox.showinfo("完成", msg)
        except Exception as e:
            messagebox.showerror("保存出错", str(e))
        finally:
            out_doc.close()

    def file_drag_start(self, event):
        self.file_drag_index = self.file_listbox.nearest(event.y)

    def file_drag_motion(self, event):
        new_idx = self.file_listbox.nearest(event.y)
        if new_idx != self.file_drag_index and self.file_drag_index is not None:
            item = self.selected_files.pop(self.file_drag_index)
            self.selected_files.insert(new_idx, item)
            self.refresh_listbox()
            self.file_listbox.select_set(new_idx)
            self.file_drag_index = new_idx

    def file_drag_end(self, event):
        self.file_drag_index = None

    def show_right_menu(self, event):
        idx = self.file_listbox.nearest(event.y)
        if 0 <= idx < self.file_listbox.size():
            self.file_listbox.select_clear(0, tk.END)
            self.file_listbox.select_set(idx)
            self.right_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def delete_selected_item(self):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        i = sel[0]
        del self.selected_files[i]
        self.refresh_listbox()


def main():
    win = tk.Tk()
    app = SimplePdfApp(win)
    app.show_file_list_view()
    win.mainloop()


if __name__ == "__main__":
    main()
