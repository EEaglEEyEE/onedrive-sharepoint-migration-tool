"""Erzeugt ein eigenstaendiges App-Icon (Wolke + Sync-Pfeile) fuer das
OneDrive/SharePoint Migration Tool - kein Nachbau des Microsoft-OneDrive-Logos,
sondern ein generisches "Cloud-Sync"-Motiv in einem aehnlichen Blauton. Erzeugt
zusaetzlich icon.ico (Windows), icon.icns (macOS) und ein Splash-Bild fuer
PyInstallers Splash-Screen-Feature (siehe onedrive-sharepoint-migration-
tool.spec)."""

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
OUT_DIR = Path(__file__).parent / "icon_build"
OUT_DIR.mkdir(exist_ok=True)

BLUE_DARK = (0, 90, 181)
BLUE_LIGHT = (39, 138, 227)
WHITE = (255, 255, 255)


def rounded_square_gradient(size: int, radius: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    for y in range(size):
        t = y / size
        r = int(BLUE_DARK[0] + (BLUE_LIGHT[0] - BLUE_DARK[0]) * t)
        g = int(BLUE_DARK[1] + (BLUE_LIGHT[1] - BLUE_DARK[1]) * t)
        b = int(BLUE_DARK[2] + (BLUE_LIGHT[2] - BLUE_DARK[2]) * t)
        for x in range(size):
            grad.putpixel((x, y), (r, g, b, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def draw_cloud(draw: ImageDraw.ImageDraw, cx: int, cy: int, scale: float, color) -> None:
    r = scale
    lobes = [
        (cx - r * 1.15, cy + r * 0.15, r * 0.85),
        (cx - r * 0.35, cy - r * 0.35, r * 1.05),
        (cx + r * 0.55, cy - r * 0.15, r * 0.95),
        (cx + r * 1.25, cy + r * 0.25, r * 0.75),
    ]
    for lx, ly, lr in lobes:
        draw.ellipse([lx - lr, ly - lr, lx + lr, ly + lr], fill=color)
    body_top = cy - r * 0.15
    body_bottom = cy + r * 0.95
    draw.rounded_rectangle(
        [cx - r * 1.55, body_top, cx + r * 1.75, body_bottom],
        radius=r * 0.8, fill=color,
    )


def draw_sync_arrows(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, color, width: int, bg_color) -> None:
    """Klassisches Sync-/Refresh-Icon (zwei gegenlaeufige C-Boegen mit
    Pfeilspitzen), als RING statt Strich konstruiert: zwei gefuellte Kreise
    (aussen minus innen) ergeben einen exakten Kreisring OHNE Unsicherheit
    ueber PILs Bogen-Endkappen. Zwei Kreissektoren (pieslice) in bg_color
    schneiden die Luecken hinein - deren Schnittkanten sind exakte radiale
    Linien. Die Pfeilspitzen setzen an GENAU diesen Kanten an (gleiche
    Innen-/Aussenradius-Punkte), dadurch fluchten Ring und Spitze
    zwangslaeufig exakt, keine Kerbe moeglich."""
    outer_r = r + width / 2
    inner_r = r - width / 2
    draw.ellipse([cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r], fill=color)
    draw.ellipse([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r], fill=bg_color)

    def pt(radius: float, angle_deg: float) -> tuple[float, float]:
        a = math.radians(angle_deg)
        return (cx + radius * math.cos(a), cy + radius * math.sin(a))

    def cut_gap(start_deg: float, end_deg: float) -> None:
        big = outer_r * 1.6
        draw.pieslice([cx - big, cy - big, cx + big, cy + big], start=start_deg, end=end_deg, fill=bg_color)

    def rounded_tail(angle_deg: float) -> None:
        mid = pt(r, angle_deg)
        rad = width / 2
        draw.ellipse([mid[0] - rad, mid[1] - rad, mid[0] + rad, mid[1] + rad], fill=color)

    def arrowhead(cut_angle: float, clockwise: bool, head_length: float, head_half_width: float) -> None:
        # Basis-Mittelpunkt liegt exakt auf der Ring-Mittellinie an der
        # Schnittkante (cut_angle) - fluchtet dadurch zwangslaeufig mit dem
        # Ring. Die Basis selbst wird aber bewusst BREITER als die reine
        # Ringbreite gemacht (radiale Richtung = perp zur Tangente), sonst
        # wird die Spitze zur duennen Nadel statt eines richtigen Dreiecks.
        tangent = math.radians(cut_angle) + (math.radians(90) if clockwise else math.radians(-90))
        radial = tangent + math.radians(90)
        base_center = pt((outer_r + inner_r) / 2, cut_angle)
        tip = (base_center[0] + head_length * math.cos(tangent), base_center[1] + head_length * math.sin(tangent))
        base1 = (base_center[0] + head_half_width * math.cos(radial), base_center[1] + head_half_width * math.sin(radial))
        base2 = (base_center[0] - head_half_width * math.cos(radial), base_center[1] - head_half_width * math.sin(radial))
        draw.polygon([base1, tip, base2], fill=color)

    # Bogen oben: Schwanz oben-links (~10 Uhr), Spitze oben-rechts (~1-2 Uhr) -
    # wachsender Winkel = im Uhrzeigersinn gezeichnet (200 -> 320).
    cut_gap(320, 390)  # Luecke rechts (schneidet 320 deg bis 30 deg frei)
    rounded_tail(210)
    arrowhead(320, clockwise=True, head_length=width * 1.7, head_half_width=width * 0.95)

    # Bogen unten: Schwanz unten-rechts (~4-5 Uhr, 30 deg), Spitze unten-links
    # (~7-8 Uhr, 140 deg) - beide Boegen laufen von kleinerem zu groesserem
    # Winkel (30 -> 140 hier, 210 -> 320 oben), also im selben Drehsinn -
    # deshalb hier ebenfalls clockwise=True, KEIN Spiegeln der Richtung.
    cut_gap(140, 210)  # Luecke links
    rounded_tail(30)
    arrowhead(140, clockwise=True, head_length=width * 1.7, head_half_width=width * 0.95)


def build_master_icon() -> Image.Image:
    img = rounded_square_gradient(SIZE, radius=int(SIZE * 0.22))
    draw = ImageDraw.Draw(img)
    # cloud_cy so gewaehlt, dass die Wolken-Silhouette vertikal zentriert ist:
    # hoechster Punkt liegt cy - scale*1.4 (Haupt-Oberlobe), tiefster Punkt
    # cy + scale*0.95 (Koerper-Unterkante) - beide gleich weit von SIZE/2.
    cloud_scale = SIZE * 0.16
    cloud_cy = int(SIZE / 2 + cloud_scale * 0.225)
    draw_cloud(draw, cx=SIZE // 2, cy=cloud_cy, scale=cloud_scale, color=WHITE)
    draw_sync_arrows(draw, cx=SIZE // 2, cy=cloud_cy - int(SIZE * 0.01), r=int(SIZE * 0.115), color=BLUE_DARK, width=int(SIZE * 0.028), bg_color=WHITE)
    return img


def find_font(size: int):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def build_splash(master: Image.Image) -> Image.Image:
    W, H = 520, 340
    splash = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    draw = ImageDraw.Draw(splash)
    draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=24, outline=(220, 224, 230, 255), width=2)

    icon_size = 128
    icon_small = master.resize((icon_size, icon_size), Image.LANCZOS)
    splash.paste(icon_small, ((W - icon_size) // 2, 34), icon_small)

    title_font = find_font(24)
    subtitle_font = find_font(14)
    title = "OneDrive / SharePoint Migration"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((W - (bbox[2] - bbox[0])) // 2, 178), title, font=title_font, fill=(20, 30, 40, 255))

    # Reservierte Zone fuer den von PyInstaller dynamisch aktualisierten
    # Ladetext (siehe Splash(..., text_pos=..., text_default=...) im .spec) -
    # hier nur eine dezente Trennlinie, der eigentliche Text wird zur
    # Laufzeit von PyInstaller selbst in dieses Bild hineingerendert.
    draw.line([(40, 216), (W - 40, 216)], fill=(230, 233, 238, 255), width=1)

    return splash


if __name__ == "__main__":
    master = build_master_icon()
    master.save(OUT_DIR / "icon_1024.png")

    # --- Windows .ico (multi-resolution) ---
    ico_path = OUT_DIR / "icon.ico"
    master.save(ico_path, sizes=[(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)])
    print("ico geschrieben:", ico_path)

    # --- macOS .icns via iconutil (braucht ein .iconset-Verzeichnis mit fest benannten Groessen) ---
    iconset_dir = OUT_DIR / "icon.iconset"
    iconset_dir.mkdir(exist_ok=True)
    icns_sizes = [16, 32, 128, 256, 512]
    for s in icns_sizes:
        master.resize((s, s), Image.LANCZOS).save(iconset_dir / f"icon_{s}x{s}.png")
        master.resize((s * 2, s * 2), Image.LANCZOS).save(iconset_dir / f"icon_{s}x{s}@2x.png")
    icns_path = OUT_DIR / "icon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)], check=True)
    print("icns geschrieben:", icns_path)

    # --- Splash-Bild ---
    splash_path = OUT_DIR / "splash.png"
    build_splash(master).save(splash_path)
    print("splash geschrieben:", splash_path)
