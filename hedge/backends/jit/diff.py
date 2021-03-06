# -*- coding: utf-8 -*-
"""Just-in-time compiling backend."""

from __future__ import division

__copyright__ = "Copyright (C) 2008 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""



import numpy
from pytools import memoize_method




class JitDifferentiator:
    def __init__(self, discr):
        self.discr = discr

    # {{{ code generation
    @memoize_method
    def make_diff(self, elgroup, dtype, shape):
        """
        :param shape: If non-square, the resulting code takes two element_ranges
          arguments and supports non-square matrices.
        """
        from hedge._internal import UniformElementRanges
        assert isinstance(elgroup.ranges, UniformElementRanges)

        ldis = elgroup.local_discretization
        discr = self.discr
        from cgen import (
                FunctionDeclaration, FunctionBody, Typedef,
                Const, Reference, Value, POD,
                Statement, Include, Line, Block, Initializer, Assign,
                For, If,
                Define)

        from pytools import to_uncomplex_dtype

        from codepy.bpl import BoostPythonModule
        mod = BoostPythonModule()

        # {{{ preamble
        S = Statement
        mod.add_to_preamble([
            Include("hedge/volume_operators.hpp"),
            Include("boost/foreach.hpp"),
            ])

        mod.add_to_module([
            S("namespace ublas = boost::numeric::ublas"),
            S("using namespace hedge"),
            S("using namespace pyublas"),
            Line(),
            Define("ROW_COUNT", shape[0]),
            Define("COL_COUNT", shape[1]),
            Define("DIMENSIONS", discr.dimensions),
            Line(),
            Typedef(POD(dtype, "value_type")),
            Typedef(POD(to_uncomplex_dtype(dtype), "uncomplex_type")),
            ])

        fdecl = FunctionDeclaration(
                    Value("void", "diff"),
                    [
                    Const(Reference(Value("uniform_element_ranges", "from_ers"))),
                    Const(Reference(Value("uniform_element_ranges", "to_ers"))),
                    Value("numpy_array<value_type>", "field")
                    ]+[
                    Value("ublas::matrix<uncomplex_type>", "diffmat_rst%d" % rst)
                    for rst in range(discr.dimensions)
                    ]+[
                    Value("numpy_array<value_type>", "result%d" % i)
                    for i in range(discr.dimensions)
                    ]
                    )
        # }}}

        # {{{ set-up
        def make_it(name, is_const=True, tpname="value_type"):
            if is_const:
                const = "const_"
            else:
                const = ""

            return Initializer(
                Value("numpy_array<%s>::%siterator" % (tpname, const), name+"_it"),
                "%s.begin()" % name)

        fbody = Block([
            If("ROW_COUNT != diffmat_rst%d.size1()" % i,
                S('throw(std::runtime_error("unexpected matrix size"))'))
            for i in range(discr.dimensions)
            ] + [
            If("COL_COUNT != diffmat_rst%d.size2()" % i,
                S('throw(std::runtime_error("unexpected matrix size"))'))
            for i in range(discr.dimensions) 
            ]+[
            If("ROW_COUNT != to_ers.el_size()",
                S('throw(std::runtime_error("unsupported image element size"))')),
            If("COL_COUNT != from_ers.el_size()",
                S('throw(std::runtime_error("unsupported preimage element size"))')),
            If("from_ers.size() != to_ers.size()",
                S('throw(std::runtime_error("image and preimage element groups '
                    'do nothave the same element count"))')),
            Line(),
            make_it("field"),
            ]+[
            make_it("result%d" % i, is_const=False)
            for i in range(discr.dimensions)
            ]+[
            Line(),
        # }}}

        # {{{ computation
            For("element_number_t eg_el_nr = 0",
                "eg_el_nr < to_ers.size()",
                "++eg_el_nr",
                Block([
                    Initializer(
                        Value("node_number_t", "from_el_base"),
                        "from_ers.start() + eg_el_nr*COL_COUNT"),
                    Initializer(
                        Value("node_number_t", "to_el_base"),
                        "to_ers.start() + eg_el_nr*ROW_COUNT"),
                    Line(),
                    For("unsigned i = 0",
                        "i < ROW_COUNT",
                        "++i",
                        Block([
                            Initializer(Value("value_type", "drst_%d" % rst), 0)
                            for rst in range(discr.dimensions)
                            ]+[
                            Line(),
                            ]+[
                            For("unsigned j = 0",
                                "j < COL_COUNT",
                                "++j",
                                Block([
                                    S("drst_%(rst)d += "
                                        "diffmat_rst%(rst)d(i, j)*field_it[from_el_base+j]"
                                        % {"rst":rst})
                                    for rst in range(discr.dimensions)
                                    ])
                                ),
                            Line(),
                            ]+[
                            Assign("result%d_it[to_el_base+i]" % rst,
                                "drst_%d" % rst)
                            for rst in range(discr.dimensions)
                            ])
                        )
                    ])
                )
            ])
        # }}}

        # {{{ compilation
        mod.add_function(FunctionBody(fdecl, fbody))

        #print "----------------------------------------------------------------"
        #print mod.generate()
        #raw_input()

        compiled_func = mod.compile(self.discr.toolchain).diff

        if self.discr.instrumented:
            from hedge.tools import time_count_flop

            compiled_func = time_count_flop(compiled_func,
                    discr.diff_timer, discr.diff_counter,
                    discr.diff_flop_counter,
                    flops=discr.dimensions*(
                        2 # mul+add
                        * ldis.node_count() * len(elgroup.members)
                        * ldis.node_count()
                        +
                        2 * discr.dimensions
                        * len(elgroup.members) * ldis.node_count()),
                    increment=discr.dimensions)

        return compiled_func
        # }}}
    # }}}

    # {{{ invocation
    def __call__(self, operators, field):
        # pick a "representative operator"
        rep_op = operators[0]

        result = [self.discr.volume_zeros(dtype=field.dtype) 
                for i in range(self.discr.dimensions)]
        from hedge.tools import is_zero
        if not is_zero(field):
            for eg in self.discr.element_groups:
                from pytools import to_uncomplex_dtype
                uncomplex_dtype = to_uncomplex_dtype(field.dtype)
                matrices = rep_op.matrices(eg)
                args = ([rep_op.preimage_ranges(eg), eg.ranges, field]
                        + [m.astype(uncomplex_dtype) for m in matrices]
                        + result)

                diff_routine = self.make_diff(eg, field.dtype,
                        matrices[0].shape)
                diff_routine(*args)

        return [result[op.rst_axis] for op in operators]
    # }}}

# vim: foldmethod=marker
