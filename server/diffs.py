"""差异对比图的独立存储。

差异热力图是「对比」的产物，不是处理结果：

- 与 ResultCache 完全隔离：文件落 data/diffs/，元数据落 diffs.json，
  绝不进入 /api/results，也就不会出现在「选择处理结果」列表里，
  更不能被当作输入再次参与对比。
- 每条记录都带来源（原图 image_id + 结果 result_id + 指标），
  对比页可以标明「这张图是哪次对比、对哪张原图哪个结果算出来的」。
- 生命周期跟随来源：删除对比记录只清差异图本身；
  删除结果/原图时级联清理依附其上的差异图，不留孤儿文件。
"""
import os
import uuid

from . import config
from .algorithms import util
from .storage import JsonStore, now_iso


class DiffStore:
    def __init__(self):
        self.store = JsonStore(config.DIFFS_JSON, {})

    # ------------------------------------------------------------------ 读
    def list(self):
        """按创建时间倒序返回所有差异对比记录。"""
        entries = list(self.store.read().values())
        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        return entries

    def get(self, diff_id):
        return self.store.read().get(diff_id)

    def find(self, image_id, result_id):
        """按来源查找已有记录（同一原图+结果的对比复用同一张差异图）。"""
        for e in self.store.read().values():
            if e.get("image_id") == image_id and e.get("result_id") == result_id:
                return e
        return None

    def file_path(self, diff_id):
        e = self.get(diff_id)
        if not e:
            return None
        p = os.path.join(config.DIFFS_DIR, e.get("file", ""))
        return p if os.path.exists(p) else None

    # ------------------------------------------------------------------ 写
    def put(self, image_id, result_id, image, metrics=None):
        """保存差异热力图并登记来源，返回 diff_id。

        同一（原图, 结果）组合只保留一条记录：重复对比复用旧图，
        避免对比次数越多、差异图越积越多。
        """
        existing = self.find(image_id, result_id)
        if existing and self.file_path(existing["diff_id"]):
            return existing["diff_id"]
        if existing:
            # 记录还在但文件丢了：清掉残记录再重写
            self.delete(existing["diff_id"])

        diff_id = uuid.uuid4().hex
        file_name = diff_id + ".png"
        dest = os.path.join(config.DIFFS_DIR, file_name)

        rgb = util.ensure_rgb(image)
        tmp = dest + ".tmp"
        rgb.save(tmp, "PNG", optimize=True)
        os.replace(tmp, dest)

        entry = {
            "diff_id": diff_id,
            "image_id": image_id,
            "result_id": result_id,
            "metrics": metrics or {},
            "file": file_name,
            "size_bytes": os.path.getsize(dest),
            "width": rgb.size[0],
            "height": rgb.size[1],
            "created_at": now_iso(),
        }

        def _upd(doc):
            doc = dict(doc)
            doc[diff_id] = entry
            return doc

        self.store.update(_upd)
        return diff_id

    def register(self, entry):
        """直接登记一条记录（供旧数据迁移使用，文件需已就位）。"""
        def _upd(doc):
            doc = dict(doc)
            doc[entry["diff_id"]] = entry
            return doc
        self.store.update(_upd)

    # ------------------------------------------------------------------ 删
    def delete(self, diff_id):
        """删除一条对比记录及其差异图文件。"""
        entry = self.get(diff_id)
        if not entry:
            return False
        self._remove_file(entry)

        def _upd(doc):
            doc = dict(doc)
            doc.pop(diff_id, None)
            return doc
        self.store.update(_upd)
        return True

    def delete_for_result(self, result_id):
        """级联：结果被删除时，清理依附它的所有差异图。"""
        return self._delete_where(lambda e: e.get("result_id") == result_id)

    def delete_for_image(self, image_id):
        """级联：原图被删除时，清理依附它的所有差异图。"""
        return self._delete_where(lambda e: e.get("image_id") == image_id)

    def _delete_where(self, pred):
        doc = self.store.read()
        victims = [e for e in doc.values() if pred(e)]
        for e in victims:
            self._remove_file(e)
        if victims:
            ids = {e["diff_id"] for e in victims}

            def _upd(d):
                return {k: v for k, v in d.items() if k not in ids}
            self.store.update(_upd)
        return len(victims)

    @staticmethod
    def _remove_file(entry):
        path = os.path.join(config.DIFFS_DIR, entry.get("file", ""))
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
