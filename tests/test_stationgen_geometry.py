import unittest

from stationgen.geometry import (
    Box,
    box_points,
    circle_points,
    oriented_rounded_box_points,
    place_shape_against_anchor,
    projection_range,
    resolve_label_alignment,
    rotated_box_points,
    rounded_box_points,
    side_direction,
    translate_points,
)


class BoxTests(unittest.TestCase):
    def test_union_inflate_and_snap(self):
        box = Box.union([Box(1.1, 2.2, 3.3, 4.4), Box(2.0, 1.0, 5.0, 3.0)])
        self.assertEqual(box, Box(1.1, 1.0, 5.0, 4.4))
        self.assertEqual(box.inflate(0.2).snap_outward(0.25), Box(0.75, 0.75, 5.25, 4.75))


class RoundedBoxPointTests(unittest.TestCase):
    def test_rounded_box_points_are_clamped_to_box(self):
        points = rounded_box_points(Box(-2, -1, 2, 1), 0.5, segments=2)

        self.assertEqual(len(points), 12)
        for x, y in points:
            self.assertGreaterEqual(x, -2)
            self.assertLessEqual(x, 2)
            self.assertGreaterEqual(y, -1)
            self.assertLessEqual(y, 1)

    def test_radius_cannot_exceed_half_size(self):
        points = rounded_box_points(Box(0, 0, 2, 1), 10, segments=1)

        self.assertIn((1.5, 0.0), points)
        self.assertIn((2.0, 0.5), points)

    def test_oriented_rounded_box_follows_requested_angle(self):
        points = oriented_rounded_box_points([(0.0, 0.0), (10.0, 10.0)], 315.0, 1.0, 0.4, None)

        diagonal_min, diagonal_max = projection_range(points, side_direction("SE"))
        cross_min, cross_max = projection_range(points, side_direction("NE"))

        self.assertGreater(diagonal_max - diagonal_min, cross_max - cross_min)

    def test_oriented_rounded_box_uses_individual_content_points(self):
        content_boxes = [Box(0.0, 0.0, 2.0, 2.0), Box(4.0, 4.0, 6.0, 6.0), Box(8.0, 8.0, 10.0, 10.0)]
        points = oriented_rounded_box_points(
            [point for box in content_boxes for point in box_points(box)],
            315.0,
            0.7,
            0.4,
            None,
        )

        diagonal_min, diagonal_max = projection_range(points, side_direction("SE"))
        cross_min, cross_max = projection_range(points, side_direction("NE"))

        self.assertGreater(diagonal_max - diagonal_min, 10.0)
        self.assertLess(cross_max - cross_min, 5.0)

    def test_center_halos_keep_angled_transfer_boxes_visually_narrow(self):
        centers = [(0.0, 0.0), (3.0, 3.0), (6.0, 6.0)]
        points = oriented_rounded_box_points(
            [point for center in centers for point in circle_points(center, 1.3)],
            315.0,
            0.7,
            0.4,
            None,
        )

        diagonal_min, diagonal_max = projection_range(points, side_direction("SE"))
        cross_min, cross_max = projection_range(points, side_direction("NE"))

        self.assertGreater(diagonal_max - diagonal_min, 12.0)
        self.assertLess(cross_max - cross_min, 4.5)


class LabelPlacementTests(unittest.TestCase):
    def test_circle_anchor_has_consistent_cardinal_and_diagonal_support(self):
        radius = 1.8
        points = circle_points((0.0, 0.0), radius)

        _east_min, east_max = projection_range(points, side_direction("E"))
        _diagonal_min, diagonal_max = projection_range(points, side_direction("SE"))

        self.assertAlmostEqual(east_max, radius)
        self.assertAlmostEqual(diagonal_max, radius)

    def test_east_label_places_left_edge_after_anchor(self):
        anchor = Box(10, 20, 14, 24)
        label = Box(-2, -1, 2, 1)

        self.assertEqual(
            place_shape_against_anchor(box_points(anchor), box_points(label), "E", 1.0),
            (17.0, 22.0),
        )

    def test_explicit_position_wins_with_nudge(self):
        anchor = Box(10, 20, 14, 24)
        label = Box(-2, -1, 2, 1)

        self.assertEqual(
            place_shape_against_anchor(
                box_points(anchor),
                box_points(label),
                "E",
                1.0,
                (0.25, -0.5),
                (1.0, 2.0),
            ),
            (1.25, 1.5),
        )

    def test_north_label_can_align_left_edge_to_anchor(self):
        anchor = Box(10, 20, 14, 24)
        label = Box(0, -1, 8, 1)

        self.assertEqual(
            place_shape_against_anchor(box_points(anchor), box_points(label), "N", 0.5, align_x="left"),
            (10.0, 18.5),
        )

    def test_south_label_can_align_right_edge_to_anchor(self):
        anchor = Box(10, 20, 14, 24)
        label = Box(0, -1, 8, 1)

        self.assertEqual(
            place_shape_against_anchor(box_points(anchor), box_points(label), "S", 0.5, align_x="right"),
            (6.0, 25.5),
        )

    def test_diagonal_label_uses_projected_clearance(self):
        anchor = Box(10, 20, 14, 24)
        relative_points = rotated_box_points(Box(0, -1, 8, 1), 315.0)
        side = "SE"
        offset = 0.25

        position = place_shape_against_anchor(box_points(anchor), relative_points, side, offset)
        placed_points = translate_points(relative_points, position)
        direction = side_direction(side)
        _anchor_min, anchor_max = projection_range(box_points(anchor), direction)
        label_min, _label_max = projection_range(placed_points, direction)

        self.assertAlmostEqual(label_min - anchor_max, offset)

    def test_diagonal_label_is_centered_along_tangent(self):
        anchor = Box(10, 20, 14, 24)
        relative_points = rotated_box_points(Box(0, -1, 8, 1), 315.0)
        side = "SE"

        position = place_shape_against_anchor(box_points(anchor), relative_points, side, 0.25)
        placed_points = translate_points(relative_points, position)
        direction = side_direction(side)
        tangent = (-direction[1], direction[0])
        anchor_min, anchor_max = projection_range(box_points(anchor), tangent)
        label_min, label_max = projection_range(placed_points, tangent)

        self.assertAlmostEqual((anchor_min + anchor_max) / 2.0, (label_min + label_max) / 2.0)

    def test_east_label_can_cross_align_to_anchor_top(self):
        anchor = Box(10, 20, 14, 24)
        label = Box(-2, -1, 2, 1)

        self.assertEqual(
            place_shape_against_anchor(box_points(anchor), box_points(label), "E", 1.0, cross_align="top"),
            (17.0, 20.0),
        )

    def test_west_label_top_cross_alignment_uses_board_top(self):
        anchor = Box(10, 20, 14, 24)
        label = Box(-2, -1, 2, 1)

        self.assertEqual(
            place_shape_against_anchor(box_points(anchor), box_points(label), "W", 1.0, cross_align="top"),
            (7.0, 20.0),
        )


class LabelAlignmentTests(unittest.TestCase):
    def test_auto_alignment_grows_plain_labels_away_from_station(self):
        self.assertEqual(resolve_label_alignment("E", "auto", "plain"), "left")
        self.assertEqual(resolve_label_alignment("NE", "auto", "plain"), "left")
        self.assertEqual(resolve_label_alignment("W", "auto", "plain"), "right")
        self.assertEqual(resolve_label_alignment("SW", "auto", "plain"), "right")
        self.assertEqual(resolve_label_alignment("N", "auto", "plain"), "center")
        self.assertEqual(resolve_label_alignment("S", "auto", "plain"), "center")

    def test_auto_alignment_keeps_knockout_labels_centered(self):
        self.assertEqual(resolve_label_alignment("E", "auto", "knockout"), "center")

    def test_explicit_alignment_wins(self):
        self.assertEqual(resolve_label_alignment("E", "right", "plain"), "right")
        self.assertEqual(resolve_label_alignment("W", "left", "knockout"), "left")


if __name__ == "__main__":
    unittest.main()
