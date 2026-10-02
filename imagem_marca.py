"""Rebranding visual de imagens oriundas de canais específicos do Telegram."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from configuracao import TelegramConfig, env_csv, env_float, env_text


def configured_chats() -> set[str]:
    """IDs autorizados a receber rebranding visual antes da publicação."""
    return set(env_csv("TG_REBRAND_CHATS"))


def enabled(chat_id) -> bool:
    return str(chat_id) in configured_chats()


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    return env_float(name, default, minimum=minimum, maximum=maximum)


def _price_box() -> tuple[float, float, float, float]:
    raw = env_text("TG_REBRAND_PRICE_BOX", "0.64,0.77,0.98,0.95")
    try:
        values = tuple(float(part.strip()) for part in raw.split(","))
    except (TypeError, ValueError):
        values = ()
    if (
        len(values) != 4
        or not all(0 <= value <= 1 for value in values)
        or values[0] >= values[2]
        or values[1] >= values[3]
    ):
        return 0.64, 0.77, 0.98, 0.95
    return values


def _font(size: int, *, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(name, max(10, int(size)))
    except OSError:
        return ImageFont.load_default()


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    start_size: int,
    *,
    bold: bool = False,
):
    size = max(10, int(start_size))
    while size > 10:
        font = _font(size, bold=bold)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
        size -= 2
    return _font(10, bold=bold)


def _centered_x(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return max(0, (width - (box[2] - box[0])) // 2)


def apply(path: str | Path) -> Path:
    """Cobre a identidade e o selo de preço da origem com a marca do Radar.

    O preço capturado não é desenhado na imagem. A região do valor vira um aviso
    para consultar a mensagem, evitando divergência entre uma arte antiga e o
    preço confirmado pelo fluxo de publicação.
    """
    source = Path(path)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")

    width, height = image.size
    if width < 120 or height < 120:
        raise ValueError("Imagem pequena demais para rebranding seguro.")

    top_ratio = _float_env("TG_REBRAND_TOP_RATIO", 0.19, 0.08, 0.35)
    box_ratio = _price_box()

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Faixa superior: cobre integralmente o banner da origem.
    top_h = max(54, min(height // 3, int(height * top_ratio)))
    draw.rectangle((0, 0, width, top_h), fill=(24, 24, 27, 255))
    accent_h = max(5, int(top_h * 0.08))
    draw.rectangle(
        (0, top_h - accent_h, width, top_h),
        fill=(255, 123, 0, 255),
    )

    title = "RADAR DE OFERTAS"
    channel = TelegramConfig.from_env().channel
    subtitle = channel if channel.startswith("@") else "Ofertas selecionadas"

    title_font = _fit_font(
        draw,
        title,
        int(width * 0.88),
        int(top_h * 0.30),
        bold=True,
    )
    subtitle_font = _fit_font(
        draw,
        subtitle,
        int(width * 0.88),
        int(top_h * 0.15),
    )

    title_box = draw.textbbox((0, 0), title, font=title_font)
    subtitle_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    title_h = title_box[3] - title_box[1]
    subtitle_h = subtitle_box[3] - subtitle_box[1]
    gap = max(4, int(top_h * 0.05))
    content_h = title_h + gap + subtitle_h
    y = max(4, (top_h - accent_h - content_h) // 2)

    draw.text(
        (_centered_x(draw, title, title_font, width), y),
        title,
        font=title_font,
        fill=(255, 255, 255, 255),
    )
    draw.text(
        (
            _centered_x(draw, subtitle, subtitle_font, width),
            y + title_h + gap,
        ),
        subtitle,
        font=subtitle_font,
        fill=(225, 225, 225, 255),
    )

    # Selo inferior: cobre o valor impresso na arte da origem. Não repetimos o
    # preço para não criar inconsistência quando a legenda tiver valor atualizado.
    x0 = int(width * box_ratio[0])
    y0 = int(height * box_ratio[1])
    x1 = int(width * box_ratio[2])
    y1 = int(height * box_ratio[3])
    radius = max(8, int(min(width, height) * 0.018))
    draw.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=radius,
        fill=(24, 24, 27, 242),
        outline=(255, 255, 255, 230),
        width=max(2, width // 260),
    )

    line1 = "OFERTA"
    line2 = "preço atual na mensagem"
    line1_font = _fit_font(
        draw,
        line1,
        int((x1 - x0) * 0.84),
        int((y1 - y0) * 0.30),
        bold=True,
    )
    line2_font = _fit_font(
        draw,
        line2,
        int((x1 - x0) * 0.84),
        int((y1 - y0) * 0.14),
    )

    line1_box = draw.textbbox((0, 0), line1, font=line1_font)
    line2_box = draw.textbbox((0, 0), line2, font=line2_font)
    line1_h = line1_box[3] - line1_box[1]
    line2_h = line2_box[3] - line2_box[1]
    gap2 = max(3, int((y1 - y0) * 0.06))
    yy = y0 + max(4, ((y1 - y0) - line1_h - gap2 - line2_h) // 2)

    draw.text(
        (
            x0 + ((x1 - x0) - (line1_box[2] - line1_box[0])) // 2,
            yy,
        ),
        line1,
        font=line1_font,
        fill=(255, 255, 255, 255),
    )
    draw.text(
        (
            x0 + ((x1 - x0) - (line2_box[2] - line2_box[0])) // 2,
            yy + line1_h + gap2,
        ),
        line2,
        font=line2_font,
        fill=(230, 230, 230, 255),
    )

    branded = Image.alpha_composite(image, overlay).convert("RGB")
    temporary = source.with_name(source.stem + ".rebrand.tmp.jpg")
    branded.save(temporary, format="JPEG", quality=92, optimize=True)
    temporary.replace(source)
    return source
