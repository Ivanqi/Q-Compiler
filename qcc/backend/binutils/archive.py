"""静态库（归档文件）支持。

get_archive 把 .a 文件解析成 Archive 对象；链接器（linker.py）在
未解析符号时从库里挑选定义了该符号的成员对象加入链接。Grouping of multiple object files into a single archive."""

import json
import logging

from qcc.backend.binutils import objectfile


def archive(objs):
    """Create an archive from multiple object files."""
    return Archive(objs)


def get_archive(filename):
    """Load an archive from file."""
    if isinstance(filename, Archive):
        return filename

    return Archive.load(filename)


class Archive:
    """The archive. Holder of object files. Similar to GNU ar."""

    logger = logging.getLogger("ar")

    def __init__(self, objs):
        self.objs = objs

    def __iter__(self):
        return iter(self.objs)

    def save(self, output_file):
        """Save archive to file."""
        self.logger.debug("Saving archive")
        # Create funky json.
        objs = [obj.serialize() for obj in self.objs]

        d = {"objects": objs}

        # Save to file:
        json.dump(d, output_file, indent=2, sort_keys=True)
        print(file=output_file)

    @classmethod
    def load(cls, f):
        """Load archive from disk."""
        cls.logger.debug("Loading archive")
        d = json.load(f)
        objs = list(map(objectfile.deserialize, d["objects"]))
        return cls(objs)
