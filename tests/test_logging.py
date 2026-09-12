"""日志配置的卫生测试：文件句柄必须被关闭。

背景（真实事故）
----------------
``reset_logging_for_tests()`` 原先只做 ``logging.getLogger("qgb").handlers.clear()``。
``TimedRotatingFileHandler`` 即使 ``delay=True`` 也会在首次写入后打开文件，
而这个文件对象被 ``clear()`` 摘掉后**没人关闭**，要等 GC 才释放 ——
于是解释器退出/GC 时刷出成片的：

    ResourceWarning: unclosed file <_io.TextIOWrapper name='...\\logs\\qgb.log'>

真实影响：CI 日志被 30+ 条这种警告淹没，真正的失败信息反而沉底；
``-W error::ResourceWarning`` 下直接变成失败。

⚠️ 写这类测试的两个坑（都踩过）
1. ``setup_logging()`` 是**幂等**的：只要 ``_CONFIGURED`` 为真就直接返回、
   不建任何 handler。所以每个用例必须先 ``reset_logging_for_tests()``，
   且必须在**断言之前**做，否则拿不到 handler。
2. 只要有一个 handler 还开着文件，Windows 上 ``TemporaryDirectory.cleanup()``
   会删不掉文件并抛出莫名的 ``NotADirectoryError``。因此这里用
   ``addCleanup`` 保证无论如何都先重置日志，再清理目录。
"""

from __future__ import annotations

import logging
import tempfile
import unittest

from qgb import logging_setup


class CloseHandlersTest(unittest.TestCase):
    def setUp(self) -> None:
        logging_setup.reset_logging_for_tests()
        # 顺序很关键：先重置日志（关掉文件句柄），再让临时目录清理
        self.addCleanup(logging_setup.reset_logging_for_tests)

    def _configure(self, td: str) -> tuple[logging.Logger, logging.FileHandler]:
        logger = logging_setup.setup_logging(log_dir=td, console=False, level=logging.INFO)
        handler = next(
            (h for h in logger.handlers if isinstance(h, logging.FileHandler)), None
        )
        self.assertIsNotNone(handler, "首次配置后应存在文件 handler")
        assert handler is not None
        return logger, handler

    def test_reset_closes_file_handler(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            logger, handler = self._configure(td)
            logger.info("触发文件打开")          # delay=True：首次写入才真正开文件

            stream = getattr(handler, "stream", None)
            self.assertIsNotNone(stream, "写入后 stream 应已打开")
            self.assertFalse(stream.closed)

            logging_setup.reset_logging_for_tests()

            self.assertTrue(stream.closed, "重配日志时必须关闭旧文件句柄")
            self.assertEqual(logger.handlers, [], "handler 应已摘除")

    def test_reset_is_idempotent(self) -> None:
        logging_setup.reset_logging_for_tests()
        logging_setup.reset_logging_for_tests()      # 不该抛异常

    def test_reconfigure_closes_previous_handler(self) -> None:
        """重配时旧句柄也必须被关掉，否则文件会一直锁着。"""
        with tempfile.TemporaryDirectory() as td:
            logger, handler = self._configure(td)
            logger.info("第一次")
            stream = handler.stream

            logging_setup.reset_logging_for_tests()
            second = logging_setup.setup_logging(log_dir=td, console=False)

            self.assertTrue(stream.closed, "旧句柄必须关闭，否则 Windows 上文件删不掉")
            self.assertIs(second, logger, "qgb 日志器是单例")

    def test_no_resource_warning_on_reset(self) -> None:
        """用 warnings 捕获器直接盯 ResourceWarning，比只看 stream 更硬。"""
        import gc
        import warnings

        with tempfile.TemporaryDirectory() as td:
            logger, _handler = self._configure(td)
            logger.info("写一条，确保文件真的被打开")
            logging_setup.reset_logging_for_tests()

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                gc.collect()

            leaked = [str(w.message) for w in caught
                      if issubclass(w.category, ResourceWarning)]
            self.assertEqual(leaked, [], "reset 之后不该再有未关闭文件句柄")


if __name__ == "__main__":
    unittest.main()
