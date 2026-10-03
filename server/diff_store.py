"""差异对比图的独立存储。

差异热力图不是「处理结果」：它只是某次对比（原图 × 结果）的可视化副产物，
因此与结果缓存彻底分开管理——

- 文件落在 data/diffs/，元数据落在 data/metadata/diffs.json，
  不进入结果缓存，/api/results 与「选择处理结果」列表永远看不到它。
- 每条记录都带来源指针（image_id + result_id + 原图名快照），
  对比页可以标明「这张差异图来自哪张原图、哪个结果」。
- 同一（原图, 结果）组合重复生成时更新原记录，不会越比越多。
- 删除对比记录即删文件；删除原图/结果时级联清理对应差异图。
"""
import os
import uuid

from PIL import Image

from . import config
from .storage import JsonStore, now_iso
from .algorithms import util


class DiffStore:
    def __init__(self):
        self.store = JsonStore(config.DIFFS_JSON, {})

    # ------------------------------------------------------------------ 读
    def list(self):
        """按创建时间倒序返回所有对比记录。"""
        recs = list(self.store.read().values())
        recs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return recs

    def get(self, diff_id):
        return self.store.read().get(diff_id)

    def find_pair(self, image_id, result_id):
        """按（原图, 结果）组合查找已有记录。"""
        for rec in self.store.read().values():
            if rec.get("image_id") == image_id and rec.get("result_id") == result_id:
                return rec
        return None

    def file_path(self, diff_id):
        rec = self.get(diff_id)
        if not rec:
            return None
        p = os.path.join(config.DIFFS_DIR, rec.get("file", ""))
        return p if os.path.exists(p) else None

    # ------------------------------------------------------------------ 写
    def put(self, image_id, result_id, image, metrics=None, image_name=""):
        """保存一张差异热力图，返回记录。

        同一（原图, 结果）组合已存在时更新原记录（替换文件与指标），
        避免重复对比生成一堆难以区分的条目。
        """
        prev = self.find_pair(image_id, result_id) or {}
        diff_id = prev.get("id") or uuid.uuid4().hex
        file_name = diff_id + ".png"
        dest = os.path.join(config.DIFFS_DIR, file_name)

        rgb = util.ensure_rgb(image)
        # 原子写：先写临时文件再 rename
        tmp = dest + ".tmp"
        rgb.save(tmp, "PNG", optimize=True)
        os.replace(tmp, dest)

        rec = {
            "id": diff_id,
            "image_id": image_id,
            "result_id": result_id,
            "image_name": image_name or prev.get("image_name", ""),
            "metrics": metrics or {},
            "file": file_name,
            "width": rgb.size[0],
            "height": rgb.size[1],
            "size_bytes": os.path.getsize(dest),
            "created_at": prev.get("created_at") or now_iso(),
            "updated_at": now_iso(),
        }
        self.store.update(lambda doc: {**doc, diff_id: rec})
        return rec

    def adopt(self, image_id, result_id, src_path, image_name="", created_at=None):
        """把一张既有差异图文件纳入管理（旧数据迁移用），返回记录或 None。

        若该（原图, 结果）组合已有记录，则保留新记录、删除传入的旧文件。
        """
        if self.find_pair(image_id, result_id):
            try:
                os.unlink(src_path)
            except OSError:
                pass
            return None
        diff_id = uuid.uuid4().hex
        file_name = diff_id + ".png"
        dest = os.path.join(config.DIFFS_DIR, file_name)
        os.replace(src_path, dest)
        try:
            with Image.open(dest) as im:
                width, height = im.size
        except Exception:
            width = height = None
        rec = {
            "id": diff_id,
            "image_id": image_id,
            "result_id": result_id,
            "image_name": image_name,
            "metrics": {},
            "file": file_name,
            "width": width,
            "height": height,
            "size_bytes": os.path.getsize(dest),
            "created_at": created_at or now_iso(),
            "updated_at": now_iso(),
        }
        self.store.update(lambda doc: {**doc, diff_id: rec})
        return rec

    # ------------------------------------------------------------------ 删
    def _delete_ids(self, ids):
        ids = set(ids)
        if not ids:
            return 0
        doc = self.store.read()
        existing = [did for did in ids if did in doc]
        if not existing:
            return 0
        for did in existing:
            path = os.path.join(config.DIFFS_DIR, doc[did].get("file", ""))
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass

        def _upd(doc):
            return {k: v for k, v in doc.items() if k not in ids}

        self.store.update(_upd)
        return len(existing)

    def delete(self, diff_id):
        """删除一条对比记录及其差异图文件。"""
        return self._delete_ids([diff_id]) > 0

    def delete_for_image(self, image_id):
        """级联：原图删除时，清理它参与的所有对比记录。"""
        return self._delete_ids([did for did, r in self.store.read().items()
                                 if r.get("image_id") == image_id])

    def delete_for_result(self, result_id):
        """级联：处理结果删除时，清理以它为输入的所有对比记录。"""
        return self._delete_ids([did for did, r in self.store.read().items()
                                 if r.get("result_id") == result_id])

    # -------------------------------------------------------------- 一致性
    def reconcile(self):
        """清理文件缺失的悬空记录与无记录引用的孤儿文件。"""
        doc = self.store.read()
        kept = {did: rec for did, rec in doc.items()
                if os.path.exists(os.path.join(config.DIFFS_DIR, rec.get("file", "")))}
        dangling = len(doc) - len(kept)
        if dangling:
            self.store.write(kept)

        removed_files = 0
        known = {rec.get("file") for rec in kept.values()}
        if os.path.isdir(config.DIFFS_DIR):
            for fn in os.listdir(config.DIFFS_DIR):
                if fn.startswith(".tmp-") or fn in known:
                    continue
                try:
                    os.unlink(os.path.join(config.DIFFS_DIR, fn))
                    removed_files += 1
                except OSError:
                    pass
        return {"dangling_meta": dangling, "orphan_files": removed_files}
