"""队列容量与「优先执行」的回归测试。

用户实测反馈：「筛选执行后为什么下载还是没有提前开始？」

根因是我给队列设了容量上限：``queue_ordered(limit=500)`` 而真实待处理有
**45,245** 条。于是「优先执行筛选结果」只调整了**前 500 条**的内部顺序 ——
用户筛出来的文件可能在第 3 万位，实际位置毫无变化，而流水线每轮只处理几十个，
看起来就是"优先执行没生效"。

实测全量排序 45,245 行只要 156 ms，所以上限本身也没有存在必要。
这里钉住两点：

1. ``queue_ordered()`` **不带参数时返回全部**待处理项（不是被截断的 500）；
2. 把**很靠后**的一批项优先执行后，它们必须出现在队首。
"""

from __future__ import annotations

import unittest

from tests.test_queue_controls import _Base


class QueueCapacityTest(_Base):
    """**回归**：队列不能被悄悄截断。"""

    def _fill(self, n: int) -> list[tuple[str, int, str]]:
        from qgb.models import GroupFile

        keys = []
        for i in range(n):
            gf = GroupFile(group_id="111", file_id=f"f{i:05d}", name=f"{i:05d}.pdf",
                           size=1000 + i, busid=1)
            self.controller.store.upsert_file(gf)
            self.controller.store.claim(gf)
            keys.append(("111", 1, f"f{i:05d}"))
        return keys

    def test_default_returns_everything(self) -> None:
        """默认（不传 limit）必须返回全部待处理项。"""
        n = 1200                    # 超过旧的 500 上限
        self._fill(n)
        queue = self.controller.queue_ordered()
        self.assertEqual(len(queue), n,
                         "队列被截断了 —— 深层的置顶/优先执行会因此失效")

    def test_prioritize_pulls_deep_item_to_front(self) -> None:
        """把第 900 位的一项优先执行，它必须到队首。"""
        self._fill(1000)
        deep = self.controller.queue_ordered()[900]
        key = (str(deep["group_id"]), int(deep["busid"]), str(deep["file_id"]))

        moved = self.controller.prioritize_filtered([key])
        self.assertEqual(moved, 1)

        after = self.controller.queue_ordered()
        pos = next(i for i, r in enumerate(after) if self.controller._key(r) == key)
        self.assertLess(pos, 3, f"优先执行后应到队首附近，实际第 {pos + 1} 位")

    def test_pinned_survives_prioritize(self) -> None:
        """优先执行不能把置顶项挤下去。"""
        self._fill(300)
        self.controller.queue_pin(("111", 1, "f00007"), True)
        self.controller.prioritize_filtered([("111", 1, "f00299"), ("111", 1, "f00298")])

        order = self.controller.queue_ordered()
        self.assertTrue(order[0]["pinned"], "置顶项应仍在队首")
        self.assertEqual(self.controller._key(order[0]), ("111", 1, "f00007"))
        self.assertIn(self.controller._key(order[1]),
                      (("111", 1, "f00299"), ("111", 1, "f00298")),
                      "优先执行的项应紧随置顶项之后")

    def test_list_files_not_truncated(self) -> None:
        """列表数据源同样不能被截断，否则筛选结果不完整。"""
        self._fill(2500)
        rows = self.controller.list_files()
        self.assertGreaterEqual(len(rows), 2500, "list_files 返回被截断")


class FilteredPriorityTest(_Base):
    """筛选 + 优先执行的组合语义。"""

    def setUp(self) -> None:
        super().setUp()
        from qgb.models import GroupFile

        # 群111：3 个；群222：3 个
        for gid in ("111", "222"):
            for i in range(3):
                gf = GroupFile(group_id=gid, file_id=f"{gid}-{i}", name=f"{gid}_{i}.pdf",
                               size=1000, busid=1)
                self.controller.store.upsert_file(gf)
                self.controller.store.claim(gf)

    def test_prioritize_only_touches_pending(self) -> None:
        """已完成的行不该出现在队列里，也不该让"优先执行"报错。"""
        from qgb.models import TransferState

        self.controller.store.mark(("111", 1, "111-0"), TransferState.DONE)
        before = len(self.controller.queue_ordered())
        moved = self.controller.prioritize_filtered([("111", 1, "111-0")])
        self.assertEqual(moved, 0, "已完成项不在队列里，移动数应为 0")
        self.assertEqual(len(self.controller.queue_ordered()), before)

    def test_unknown_keys_ignored(self) -> None:
        moved = self.controller.prioritize_filtered([("111", 1, "nope")])
        self.assertEqual(moved, 0)

    def test_empty_input_is_safe(self) -> None:
        self.assertEqual(self.controller.prioritize_filtered([]), 0)


def load_tests(loader, tests, pattern):  # noqa: ARG001
    suite = unittest.TestSuite()
    for cls_name in ("QueueCapacityTest", "FilteredPriorityTest"):
        cls = globals()[cls_name]
        for name in sorted(n for n in dir(cls) if n.startswith("test_")):
            suite.addTest(cls(name))
    return suite


if __name__ == "__main__":
    unittest.main()
