import cv2
import numpy as np

from src.processors.constants import (
    DOT_AREA_TYPES_IN_ORDER,
    EDGE_TYPES_IN_ORDER,
    LINE_AREA_TYPES_IN_ORDER,
    AreaTemplate,
    EdgeType,
    ScannerType,
    WarpMethod,
)
from src.processors.internal.CropOnPatchesCommon import CropOnPatchesCommon
from src.utils.image import ImageUtils
from src.utils.interaction import InteractionUtils
from src.utils.math import MathUtils


class CropOnDotLines(CropOnPatchesCommon):
    __is_internal_preprocessor__ = True

    scan_area_templates_for_layout = {
        "ONE_LINE_TWO_DOTS": [
            AreaTemplate.topRightDot,
            AreaTemplate.bottomRightDot,
            AreaTemplate.leftLine,
        ],
        "TWO_DOTS_ONE_LINE": [
            AreaTemplate.rightLine,
            AreaTemplate.topLeftDot,
            AreaTemplate.bottomLeftDot,
        ],
        "TWO_LINES": [
            AreaTemplate.leftLine,
            AreaTemplate.rightLine,
        ],
        "TWO_LINES_HORIZONTAL": [
            AreaTemplate.topLine,
            AreaTemplate.bottomLine,
        ],
        "FOUR_DOTS": DOT_AREA_TYPES_IN_ORDER,
    }

    default_scan_area_descriptions = {
        **{
            marker_type: {
                "scannerType": ScannerType.PATCH_DOT,
                "selector": "SELECT_CENTER",
            }
            for marker_type in DOT_AREA_TYPES_IN_ORDER
        },
        **{
            marker_type: {
                "scannerType": ScannerType.PATCH_LINE,
                "selector": "LINE_OUTER_EDGE",
            }
            for marker_type in LINE_AREA_TYPES_IN_ORDER
        },
        "CUSTOM": {},
    }

    default_points_selector_map = {
        "CENTERS": {
            AreaTemplate.topLeftDot: "SELECT_CENTER",
            AreaTemplate.topRightDot: "SELECT_CENTER",
            AreaTemplate.bottomRightDot: "SELECT_CENTER",
            AreaTemplate.bottomLeftDot: "SELECT_CENTER",
            AreaTemplate.leftLine: "LINE_OUTER_EDGE",
            AreaTemplate.rightLine: "LINE_OUTER_EDGE",
        },
        "INNER_WIDTHS": {
            AreaTemplate.topLeftDot: "SELECT_TOP_RIGHT",
            AreaTemplate.topRightDot: "SELECT_TOP_LEFT",
            AreaTemplate.bottomRightDot: "SELECT_BOTTOM_LEFT",
            AreaTemplate.bottomLeftDot: "SELECT_BOTTOM_RIGHT",
            AreaTemplate.leftLine: "LINE_INNER_EDGE",
            AreaTemplate.rightLine: "LINE_INNER_EDGE",
        },
        "INNER_HEIGHTS": {
            AreaTemplate.topLeftDot: "SELECT_BOTTOM_LEFT",
            AreaTemplate.topRightDot: "SELECT_BOTTOM_RIGHT",
            AreaTemplate.bottomRightDot: "SELECT_TOP_RIGHT",
            AreaTemplate.bottomLeftDot: "SELECT_TOP_LEFT",
            AreaTemplate.leftLine: "LINE_OUTER_EDGE",
            AreaTemplate.rightLine: "LINE_OUTER_EDGE",
        },
        "INNER_CORNERS": {
            AreaTemplate.topLeftDot: "SELECT_BOTTOM_RIGHT",
            AreaTemplate.topRightDot: "SELECT_BOTTOM_LEFT",
            AreaTemplate.bottomRightDot: "SELECT_TOP_LEFT",
            AreaTemplate.bottomLeftDot: "SELECT_TOP_RIGHT",
            AreaTemplate.leftLine: "LINE_INNER_EDGE",
            AreaTemplate.rightLine: "LINE_INNER_EDGE",
        },
        "OUTER_CORNERS": {
            AreaTemplate.topLeftDot: "SELECT_TOP_LEFT",
            AreaTemplate.topRightDot: "SELECT_TOP_RIGHT",
            AreaTemplate.bottomRightDot: "SELECT_BOTTOM_RIGHT",
            AreaTemplate.bottomLeftDot: "SELECT_BOTTOM_LEFT",
            AreaTemplate.leftLine: "LINE_OUTER_EDGE",
            AreaTemplate.rightLine: "LINE_OUTER_EDGE",
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tuning_options = self.tuning_options
        self.line_kernel_morph = cv2.getStructuringElement(
            cv2.MORPH_RECT, tuple(tuning_options.get("lineKernel", [2, 10]))
        )
        self.dot_kernel_morph = cv2.getStructuringElement(
            cv2.MORPH_RECT, tuple(tuning_options.get("dotKernel", [5, 5]))
        )

    def validate_and_remap_options_schema(self, options):
        layout_type = options["type"]
        tuning_options = options["tuningOptions"]
        parsed_options = {
            "pointsLayout": layout_type,
            "enableCropping": True,
            "tuningOptions": {
                "warpMethod": tuning_options.get(
                    "warpMethod", WarpMethod.PERSPECTIVE_TRANSFORM
                )
            },
        }

        # TODO: add default values for provided options["scanAreas"]? like get "maxPoints" from options["lineMaxPoints"]
        # inject scanAreas
        parsed_options["scanAreas"] = [
            {
                "areaTemplate": area_template,
                "areaDescription": options.get(area_template, {}),
                "customOptions": {},
            }
            for area_template in self.scan_area_templates_for_layout[layout_type]
        ]
        return parsed_options

    edge_selector_map = {
        AreaTemplate.topLine: {
            "LINE_INNER_EDGE": EdgeType.BOTTOM,
            "LINE_OUTER_EDGE": EdgeType.TOP,
        },
        AreaTemplate.leftLine: {
            "LINE_INNER_EDGE": EdgeType.RIGHT,
            "LINE_OUTER_EDGE": EdgeType.LEFT,
        },
        AreaTemplate.bottomLine: {
            "LINE_INNER_EDGE": EdgeType.TOP,
            "LINE_OUTER_EDGE": EdgeType.BOTTOM,
        },
        AreaTemplate.rightLine: {
            "LINE_INNER_EDGE": EdgeType.LEFT,
            "LINE_OUTER_EDGE": EdgeType.RIGHT,
        },
    }

    @staticmethod
    def select_edge_from_scan_area(area_description, edge_type):
        destination_rectangle = MathUtils.get_rectangle_points_from_box(
            area_description["origin"], area_description["dimensions"]
        )

        destination_line = MathUtils.select_edge_from_rectangle(
            destination_rectangle, edge_type
        )
        return destination_line

    def find_and_select_points_from_line(self, image, area_description, _file_path):
        area_label = area_description["label"]
        points_selector = area_description.get(
            "selector", self.default_points_selector[area_label]
        )

        line_edge_contours = self.find_line_edges_from_options(
            image, area_description, area_label
        )

        edge_type = self.edge_selector_map[area_label][points_selector]
        source_contour = line_edge_contours[edge_type]

        destination_line = self.select_edge_from_scan_area(area_description, edge_type)

        max_points = area_description.get("maxPoints", None)
        # Extrapolates the destination_line to get approximate destination points
        (
            control_points,
            destination_points,
        ) = ImageUtils.get_control_destination_points_from_contour(
            source_contour, destination_line, max_points
        )
        return control_points, destination_points

    def find_line_edges_from_options(self, image, area_description, area_label):
        config = self.tuning_config
        tuning_options = self.tuning_options
        area, area_start = self.compute_scan_area_util(image, area_description)

        # Make boxes darker (less gamma)
        darker_image = ImageUtils.adjust_gamma(area, config.thresholding.GAMMA_LOW)

        # Lines are expected to be fairly dark
        line_threshold = tuning_options.get("lineThreshold", 180)

        _, thresholded = cv2.threshold(
            darker_image, line_threshold, 255, cv2.THRESH_TRUNC
        )
        normalised = ImageUtils.normalize(thresholded)

        # add white padding
        kernel_height, kernel_width = self.line_kernel_morph.shape[:2]
        white, pad_range = ImageUtils.pad_image_from_center(
            normalised, kernel_width, kernel_height, 255
        )

        # Threshold-Normalize after morph + white padding
        _, white_thresholded = cv2.threshold(
            white, line_threshold, 255, cv2.THRESH_TRUNC
        )
        white_normalised = ImageUtils.normalize(white_thresholded)

        # Open : erode then dilate
        line_morphed = cv2.morphologyEx(
            white_normalised, cv2.MORPH_OPEN, self.line_kernel_morph, iterations=3
        )

        # remove white padding
        line_morphed = line_morphed[
            pad_range[0] : pad_range[1], pad_range[2] : pad_range[3]
        ]

        if config.outputs.show_image_level >= 5:
            self.debug_hstack += [
                darker_image,
                normalised,
                white_thresholded,
                line_morphed,
            ]
        elif config.outputs.show_image_level == 4:
            InteractionUtils.show(
                f"morph_opened_{area_label}", line_morphed, pause=False
            )

        # Note: points are returned in the order of order_four_points: (tl, tr, br, bl)
        (
            _,
            edge_contours_map,
        ) = self.find_morph_corners_and_contours_map(
            area_start, line_morphed, area_description
        )

        if edge_contours_map is None:
            raise Exception(
                f"No line match found at origin: {area_description['origin']} with dimensions: { area_description['dimensions']}"
            )
        return edge_contours_map

    def find_dot_corners_from_options(self, image, area_description, _file_path):
        config = self.tuning_config
        tuning_options = self.tuning_options

        area, area_start = self.compute_scan_area_util(image, area_description)

        # TODO: simple colored thresholding to clear out noise?

        blur_size = tuning_options.get("dotBlur", None)
        if blur_size:
            area = cv2.GaussianBlur(area, (blur_size, blur_size), 0)

        # Open : erode then dilate
        morph_c = cv2.morphologyEx(
            area, cv2.MORPH_OPEN, self.dot_kernel_morph, iterations=3
        )

        # Dots are expected to be fairly dark
        dot_threshold = tuning_options.get("dotThreshold", 150)
        _, thresholded = cv2.threshold(morph_c, dot_threshold, 255, cv2.THRESH_TRUNC)
        normalised = ImageUtils.normalize(thresholded)

        if config.outputs.show_image_level >= 5:
            self.debug_hstack += [area, morph_c, thresholded, normalised]

        corners, _ = self.find_morph_corners_and_contours_map(
            area_start, normalised, area_description
        )
        if corners is None:
            hstack = ImageUtils.get_padded_hstack([self.debug_image, area, thresholded])
            InteractionUtils.show(
                f"No patch/dot debug hstack",
                ImageUtils.get_padded_hstack(self.debug_hstack),
                pause=0,
            )
            InteractionUtils.show(f"No patch/dot found:", hstack, pause=1)
            raise Exception(
                f"No patch/dot found at origin: {area_description['origin']} with dimensions: { area_description['dimensions']}"
            )

        return corners

    # TODO: create a ScanArea class and move some methods there
    def find_morph_corners_and_contours_map(self, area_start, area, area_description):
        scanner_type, area_label = (
            area_description["scannerType"],
            area_description["label"],
        )
        config = self.tuning_config
        edge = cv2.Canny(area, 185, 55)

        if config.outputs.show_image_level >= 5:
            self.debug_hstack.append(edge.copy())

        # Should mostly return a single contour in the area
        all_contours = ImageUtils.grab_contours(
            cv2.findContours(edge, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        )

        # convexHull to resolve disordered curves due to noise
        all_contours = [cv2.convexHull(c) for c in all_contours]

        if len(all_contours) == 0:
            return None, None
        ordered_patch_corners, edge_contours_map = None, None
        bounding_contour = sorted(all_contours, key=cv2.contourArea, reverse=True)[0]

        if scanner_type == ScannerType.PATCH_DOT:
            # Bounding rectangle will not be rotated
            x, y, w, h = cv2.boundingRect(bounding_contour)
            patch_corners = MathUtils.get_rectangle_points(x, y, w, h)
            (
                _,
                edge_contours_map,
            ) = ImageUtils.split_patch_contour_on_corners(
                patch_corners, bounding_contour
            )
        if scanner_type == ScannerType.PATCH_LINE:
            # Rotated rectangle can correct slight rotations better
            rotated_rect = cv2.minAreaRect(bounding_contour)
            # TODO: less confidence if angle = rotated_rect[2] is too skew
            rotated_rect_points = cv2.boxPoints(rotated_rect)
            patch_corners = np.intp(rotated_rect_points)
            (
                ordered_patch_corners,
                edge_contours_map,
            ) = ImageUtils.split_patch_contour_on_corners(
                patch_corners, bounding_contour
            )

        # TODO: less confidence if given dimensions differ from matched block size (also give a warning)
        if config.outputs.show_image_level >= 5:
            if ordered_patch_corners is not None:
                ImageUtils.draw_contour(edge, ordered_patch_corners)
            self.debug_hstack.append(edge)
            InteractionUtils.show(
                f"Debug Largest Patch: {area_label}",
                ImageUtils.get_padded_hstack(self.debug_hstack),
                0,
            )
            self.debug_vstack.append(self.debug_hstack)
            self.debug_hstack = []

        absolute_corners = MathUtils.shift_points_from_origin(
            area_start, ordered_patch_corners
        )

        shifted_edge_contours_map = {
            edge_type: MathUtils.shift_points_from_origin(
                area_start, edge_contours_map[edge_type]
            )
            for edge_type in EDGE_TYPES_IN_ORDER
        }

        return absolute_corners, shifted_edge_contours_map