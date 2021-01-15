import ufoLib2
from collections import OrderedDict, defaultdict
import re
from fontTools.pens.roundingPen import RoundingPen
from ufo2ft.featureCompiler import parseLayoutFeatures

REF_MARK_NAME = "abvm.e"
REF_GLYPH_NAME = "dvmE"


def interpolateGlyph(glyph1, glyph2, value, new_glyph):
    from fontMath import MathGlyph
    math_glyph1 = MathGlyph(glyph1)
    math_glyph2 = MathGlyph(glyph2)
    inter_math_glyph = math_glyph1 + (math_glyph2 - math_glyph1) * value
    inter_math_glyph.round()
    pen = new_glyph.getPen()
    rounding_pen = RoundingPen(pen)
    inter_math_glyph.draw(rounding_pen)
    inter_math_glyph.round()
    new_glyph.width = inter_math_glyph.width
    new_glyph.anchors = inter_math_glyph.anchors


def get_anchor_right_margin(glyph, anchor_name, prefix="",
                        ref_mark_name=REF_MARK_NAME):
    for anchor in glyph.anchors:
        if anchor.name == prefix + ref_mark_name:
            anchorshift = glyph.width - anchor.x
    return anchorshift


class MatraMaker():
    def __init__(self, fonts, threshold_left=32, threshold_right=32):
        self.fonts = fonts
        self.threshold_left = threshold_left
        self.threshold_right = threshold_right

        self._glyphs_matra_widths = None
        self._bucketed_matra_widths = None

    def _font_matra_widths(self, font, matra_width, bases,
                          ref_glyph_name=REF_GLYPH_NAME,
                          ref_mark_name=REF_MARK_NAME):
        """
        Get base widths values
        """
        refglyph = font[ref_glyph_name]

        ref_shift = (
            refglyph.width - get_anchor_right_margin(
                refglyph, ref_mark_name, prefix="_")
        )

        width_values = {}
        for name in sorted(bases):
            glyph = font[name]
            ref_anchor = None
            for anchor in glyph.anchors:
                if anchor.name == ref_mark_name:
                    ref_anchor = anchor
                    # break
            if ref_anchor is not None:
                width_values[name] = (
                    glyph.width - get_anchor_right_margin(glyph, ref_mark_name)
                )
            else:
                width_values[name] = glyph.width - abs(ref_shift)

        width_values = {k: v + matra_width for (k, v) in width_values.items()}
        return width_values

    @property
    def fonts_matra_widths(self):
        fonts = self.fonts
        bases = self.bases
        fonts_matra_widths = OrderedDict()
        for font in fonts:
            matra_glyph = font["dvmI"]
            matra_width = matra_glyph.width
            fonts_matra_widths[font.path] = self._font_matra_widths(font, matra_width,
                                                                   bases)
        return fonts_matra_widths

    @property
    def glyphs_matra_widths(self):
        if self._glyphs_matra_widths:
            return self._glyphs_matra_widths
        glyphs_matra_widths = OrderedDict()
        for key, values in self.fonts_matra_widths.items():
            for name, width in values.items():
                if name not in glyphs_matra_widths:
                    glyphs_matra_widths[name] = []
                glyphs_matra_widths[name].append(width)

        self._glyphs_matra_widths = glyphs_matra_widths
        return glyphs_matra_widths

    def generate_matra_variants(self, glyphs_matra_widths):
        fonts = self.fonts

        for font in fonts:
            for name in [g.name for g in font if re.match("dvmI\.[0-9]+", g.name)]:
                del font[name]

        for font_index, font in enumerate(fonts):
            short_glyph = font["dvmI_short"]
            long_glyph = font["dvmI_long"]
            short_glyph_value = short_glyph.getBounds()[2]
            long_glyph_value = long_glyph.getBounds()[2]

            variant_index = 0
            for key, value in sorted(glyphs_matra_widths.items()):
                variant_index += 1
                # key_string = "".join(key)
                key_string = "%03i" % variant_index
                new_glyph = font.newGlyph("dvmI.%s" % str(key_string))
                # new_glyph = font.newGlyph("dvmI.%03i" % i)
                matra_width = glyphs_matra_widths[key][font_index]
                value = (
                    (matra_width - short_glyph_value) /
                    (long_glyph_value - short_glyph_value)
                )
                interpolateGlyph(short_glyph, long_glyph, value, new_glyph)

    @property
    def bucketed_matra_widths(self):
        """
        Converts glyphs_matra_widths {glyphname: tuple of matra width per font}
        to bucketed_matra_widths {set of glyphs: tuple of matra width per font}
        with the intermediary buckets {tuple of matra width per font: set of glyphs}
        """
        if self._bucketed_matra_widths:
            return self._bucketed_matra_widths
        glyphs_matra_widths = self.glyphs_matra_widths
        threshold_left = self.threshold_left
        threshold_right = self.threshold_right
        bucketed_matra_widths = OrderedDict()
        buckets = OrderedDict()
        for matra_widths, glyph_name in sorted(
                (v, k) for (k, v) in glyphs_matra_widths.items()):
            value = tuple(matra_widths)
            name = glyph_name
            if not buckets:
                buckets[value] = set((name,))
            else:
                bucketed = False
                for bucket_key in buckets:
                    if all(v + threshold_right > k >= v - threshold_left
                           for (k, v) in zip(bucket_key, value)):
                        buckets[bucket_key].add(name)
                        bucketed = True
                        break
                if not bucketed:
                    buckets[value] = set((name,))
        bucketed_matra_widths = OrderedDict(
            (tuple(sorted(k)), v) for (v, k) in buckets.items()
        )
        self._bucketed_matra_widths = bucketed_matra_widths
        return bucketed_matra_widths

    @property
    def bases(self):
        "Collect the base glyphs that need to have matching I matras."
        fonts = self.fonts
        bases = set()
        for font in fonts:
            fea = parseLayoutFeatures(font)
            for statement in fea.statements:
                if (hasattr(statement, "glyphs") and hasattr(statement, "name") and
                        statement.name == "BASES_ALIVE"):
                    bases |= set(statement.glyphs.glyphs)
            if bases: break
        return bases

    def feature_text(self, glyphs_matra_widths):
        mi_variants = []
        substitutions = []
        variant_index = 0
        for key in sorted(glyphs_matra_widths.keys()):
            variant_index += 1
            # key_string = "".join(key)
            key_string = "%03i" % variant_index
            variant_name = "dvmI.%s" % str(key_string)
            mi_variants.append(variant_name)
            substitutions.append(
                "sub %s %s by %s;" % (
                    "dvmI'",
                    # "[%s]" % ",".join(key),
                    "[%s]" % " ".join(sorted(key)),
                    variant_name,
                )
            )
        text = ("@mI_VARIANTS = [\n" + "\n".join(mi_variants) + "\n];\n")
        text += ("lookup matras_i {\n" + "\n".join(substitutions) + "\n} matras_i;\n")

        return text


def main(args=None):
    import argparse
    parser = argparse.ArgumentParser(
        description="Generates I matra glyphs and pres feature lookup in FEA syntax"
    )
    parser.add_argument(
        'threshold', type=int, nargs=2,
        help="Left and right threshold to find a match.",
    )
    parser.add_argument(
        "output",
        help="Output for FEA lookup."
    )
    parser.add_argument(
        'ufos', nargs="+",
        help="UFOs to process."
    )
    options = parser.parse_args(args)

    fonts = []
    for path in options.ufos:
        fonts.append(ufoLib2.Font(path))

    matra_maker = MatraMaker(fonts, options.threshold[0], options.threshold[1])

    matra_maker.generate_matra_variants(matra_maker.bucketed_matra_widths)

    for font in fonts:
        font.save()

    with open(options.output, "w") as fp:
        fp.write(matra_maker.feature_text(matra_maker.bucketed_matra_widths))

if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
