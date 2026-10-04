import io
import unittest

from qcc.midend import ir
from qcc.backend.arch.example import ExampleArch
from qcc.midend.irutils import verify_module
from qcc.frontend.c import CBuilder, CSynthesizer
from qcc.frontend.c.options import COptions


class CSynthesizerTestCase(unittest.TestCase):
    def test_hello(self):
        """Convert C to Ir, and then this IR to C"""
        src = r"""
        void printf(char*);
        void main(int b) {
          printf("Hello" "world\n");
        }
        """
        arch = ExampleArch()
        builder = CBuilder(arch.info, COptions())
        f = io.StringIO(src)
        ir_module = builder.build(f, None)
        assert isinstance(ir_module, ir.Module)
        verify_module(ir_module)
        synthesizer = CSynthesizer()
        synthesizer.syn_module(ir_module)


if __name__ == "__main__":
    unittest.main()
