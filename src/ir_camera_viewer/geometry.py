"""One geometric convention for the preview, cursor mapping, and snapshots."""

from __future__ import annotations

import math
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QTransform

from .models import Orientation


def orientation_transform(width: int, height: int, orientation: Orientation) -> QTransform:
    angle = math.radians(orientation.angle % 360)
    cosine, sine = math.cos(angle), math.sin(angle)
    cosine = 0.0 if abs(cosine) < 1e-12 else cosine
    sine = 0.0 if abs(sine) < 1e-12 else sine
    sx = -1 if orientation.flip_horizontal else 1
    sy = -1 if orientation.flip_vertical else 1
    a, b, c, d = cosine*sx, -sine*sy, sine*sx, cosine*sy
    base = QTransform(a, c, b, d, 0, 0)
    bounds = base.mapRect(QRectF(0, 0, width, height))
    return QTransform(a, c, b, d, -bounds.left(), -bounds.top())


def grayscale_qimage(image: np.ndarray) -> QImage:
    if image.dtype != np.uint8 or image.ndim != 2:
        raise ValueError("Display images must be 2D uint8 arrays.")
    image = np.ascontiguousarray(image)
    height, width = image.shape
    # Own the storage independently of a temporary NumPy array.
    return QImage(image.data, width, height, image.strides[0], QImage.Format.Format_Grayscale8).copy()


def oriented_pixels(image: np.ndarray, orientation: Orientation) -> np.ndarray:
    if math.isclose(orientation.angle / 90, round(orientation.angle / 90), abs_tol=1e-10):
        result = image
        if orientation.flip_horizontal:
            result = result[:, ::-1]
        if orientation.flip_vertical:
            result = result[::-1, :]
        return np.ascontiguousarray(np.rot90(result, -round(orientation.angle / 90)))
    height, width = image.shape
    transform = orientation_transform(width, height, orientation)
    qimage = grayscale_qimage(image).transformed(transform, Qt.TransformationMode.SmoothTransformation)
    qimage = qimage.convertToFormat(QImage.Format.Format_Grayscale8)
    pixels = np.frombuffer(qimage.constBits(), np.uint8).reshape(qimage.height(), qimage.bytesPerLine())
    return pixels[:, :qimage.width()].copy()
