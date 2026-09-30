import cv2
import numpy as np
import subprocess
import os
import sys

# Использование:
# python neon_filter_v3.py input.mp4 output_neon.mp4
INPUT = sys.argv[1] if len(sys.argv) > 1 else "input.mp4"
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else "output_neon.mp4"
TEMP = "temp_neon_v3.avi"

# V3: сильнее плавление, меньше деталей, больше жёлтого
WARP_AMOUNT = 0.055       # сильная органическая деформация от размера кадра
WARP_CHANGE_TIME = 1.35   # время плавной смены карты деформации, секунды
POSTER_LEVELS = 8         # крупные цветовые области, меньше узнаваемых деталей
EDGE_STRENGTH = 0.48      # сила темно-зеленых контуров
GLOW_STRENGTH = 0.12      # мягкое свечение
SATURATION = 1.10         # насыщенность
RANDOM_SEED = 418         # фиксированное движение при каждом запуске


def make_noise_field(height, width, rng, coarse_divisor):
    """Создает мягкую нерегулярную карту смещения без полос и синусоидальных волн."""
    small_w = max(3, int(np.ceil(width / coarse_divisor)))
    small_h = max(3, int(np.ceil(height / coarse_divisor)))

    low = rng.normal(0.0, 1.0, (small_h, small_w)).astype(np.float32)
    field = cv2.resize(low, (width, height), interpolation=cv2.INTER_CUBIC)

    sigma = max(5.0, min(width, height) * 0.018)
    field = cv2.GaussianBlur(field, (0, 0), sigmaX=sigma, sigmaY=sigma)

    field -= field.mean()
    std = field.std()
    if std > 1e-6:
        field /= std

    return np.clip(field, -2.2, 2.2)


def new_displacement_pair(height, width, rng):
    """Смешивает крупную и мелкую органическую деформацию."""
    coarse_x = make_noise_field(height, width, rng, 115)
    coarse_y = make_noise_field(height, width, rng, 115)
    fine_x = make_noise_field(height, width, rng, 48)
    fine_y = make_noise_field(height, width, rng, 48)

    field_x = coarse_x * 0.68 + fine_x * 0.32
    field_y = coarse_y * 0.68 + fine_y * 0.32
    return field_x.astype(np.float32), field_y.astype(np.float32)


def neon_gradient(gray):
    """Темно-зеленые тени и желто-лаймовые света."""
    points = np.array([0, 28, 58, 88, 118, 142, 158, 205, 255], dtype=np.float32)

    # BGR-цвета: в светах больше желтого, чем чистого зеленого
    colors = np.array([
        [1, 13, 1],
        [3, 38, 5],
        [5, 76, 10],
        [8, 122, 17],
        [12, 176, 27],
        [22, 224, 58],
        [33, 250, 164],
        [46, 255, 228],
        [112, 250, 255],
    ], dtype=np.float32)

    output_channels = []
    flat = gray.reshape(-1)

    for channel in range(3):
        mapped = np.interp(flat, points, colors[:, channel])
        output_channels.append(mapped.reshape(gray.shape).astype(np.uint8))

    return cv2.merge(output_channels)


def process_video():
    if not os.path.isfile(INPUT):
        raise FileNotFoundError(f"Не найден входной файл: {INPUT}")

    cap = cv2.VideoCapture(INPUT)
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видео: {INPUT}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    writer = cv2.VideoWriter(
        TEMP,
        cv2.VideoWriter_fourcc(*"MJPG"),
        fps,
        (width, height),
    )

    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Не удалось создать временное видео")

    grid_x, grid_y = np.meshgrid(
        np.arange(width, dtype=np.float32),
        np.arange(height, dtype=np.float32),
    )

    rng = np.random.default_rng(RANDOM_SEED)
    current_x, current_y = new_displacement_pair(height, width, rng)
    next_x, next_y = new_displacement_pair(height, width, rng)

    segment_frames = max(1, int(round(WARP_CHANGE_TIME * fps)))
    displacement_pixels = max(4.0, min(width, height) * WARP_AMOUNT)

    frame_number = 0
    current_segment = 0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            segment = frame_number // segment_frames
            if segment != current_segment:
                current_x, current_y = next_x, next_y
                next_x, next_y = new_displacement_pair(height, width, rng)
                current_segment = segment

            phase = (frame_number % segment_frames) / float(segment_frames)
            blend = 0.5 - 0.5 * np.cos(np.pi * phase)

            displacement_x = current_x * (1.0 - blend) + next_x * blend
            displacement_y = current_y * (1.0 - blend) + next_y * blend

            map_x = grid_x + displacement_x * displacement_pixels
            map_y = grid_y + displacement_y * displacement_pixels

            warped = cv2.remap(
                frame,
                map_x.astype(np.float32),
                map_y.astype(np.float32),
                interpolation=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT_101,
            )

            # Легкое cartoon-сглаживание без уничтожения деталей
            smooth = cv2.bilateralFilter(warped, 11, 75, 75)
            gray = cv2.cvtColor(smooth, cv2.COLOR_BGR2GRAY)
            gray = cv2.convertScaleAbs(gray, alpha=1.32, beta=-18)

            # Мягкая постеризация с большим количеством уровней
            step = 256.0 / POSTER_LEVELS
            poster = np.floor(gray.astype(np.float32) / step) * step + step * 0.5
            poster = np.clip(poster, 0, 255).astype(np.uint8)

            neon = neon_gradient(poster)

            # Тонкие темно-зеленые контуры
            edges = cv2.Canny(gray, 55, 125)
            edges = cv2.GaussianBlur(edges, (3, 3), 0.65)
            edge_mask = (edges.astype(np.float32) / 255.0 * EDGE_STRENGTH)[:, :, None]

            outline_color = np.empty_like(neon)
            outline_color[:, :] = (2, 37, 4)

            result = (
                neon.astype(np.float32) * (1.0 - edge_mask)
                + outline_color.astype(np.float32) * edge_mask
            ).astype(np.uint8)

            # Слабое свечение, чтобы не размывать контуры
            glow_sigma = max(2.0, min(width, height) * 0.006)
            glow = cv2.GaussianBlur(neon, (0, 0), glow_sigma)
            result = cv2.addWeighted(result, 1.0, glow, GLOW_STRENGTH, 0)

            # Умеренная насыщенность
            hsv = cv2.cvtColor(result, cv2.COLOR_BGR2HSV)
            hsv[:, :, 1] = np.clip(
                hsv[:, :, 1].astype(np.float32) * SATURATION,
                0,
                255,
            ).astype(np.uint8)
            result = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

            writer.write(result)
            frame_number += 1
            print(
                f"\rОбработано кадров: {frame_number}/{frame_count}",
                end="",
                flush=True,
            )
    finally:
        cap.release()
        writer.release()

    print("\nFFmpeg: кодирование и возврат исходного звука...")

    try:
        subprocess.run([
            "ffmpeg",
            "-y",
            "-i", TEMP,
            "-i", INPUT,
            "-map", "0:v:0",
            "-map", "1:a?",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "17",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            OUTPUT,
        ], check=True)
    finally:
        if os.path.exists(TEMP):
            os.remove(TEMP)

    print(f"Готово: {OUTPUT}")


if __name__ == "__main__":
    process_video()
