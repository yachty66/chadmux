"""Replace every cmux logo asset with the chadmux logo, keeping each file's size, shape and channel badge."""
import base64, io, json, os, sys
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MASTER = Image.open(os.path.join(ROOT, "design/chadmux/chadmux-square-1024.png")).convert("RGBA")


def squircle(size, radius_frac=0.225):
    ss = 4
    m = Image.new("L", (size * ss, size * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size * ss - 1, size * ss - 1), radius=int(size * ss * radius_frac), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def tile(size, rounded):
    t = MASTER.resize((size, size), Image.LANCZOS)
    if rounded:
        t.putalpha(squircle(size))
    return t


def badge_box(old):
    """Bounding box of a full-width colored channel band (NIGHTLY/RC/DEV) near the bottom, or None."""
    w, h = old.size
    px = old.convert("RGB").load()
    def colored(x, y):
        r, g, b = px[x, y]
        mx, mn = max(r, g, b), min(r, g, b)
        return mx - mn > 60
    rows = [y for y in range(h // 2, h) if sum(colored(x, y) for x in range(0, w, max(1, w // 64))) > 0.5 * len(range(0, w, max(1, w // 64)))]
    if not rows:
        return None
    y0, y1 = min(rows), max(rows)
    mid = (y0 + y1) // 2
    cols = [x for x in range(w) if colored(x, mid)]
    return (min(cols), y0, max(cols) + 1, y1 + 1)


def replace_png(path):
    old = Image.open(path).convert("RGBA")
    w, h = old.size
    a = old.getchannel("A")
    bbox = a.point(lambda v: 255 if v > 128 else 0).getbbox() or (0, 0, w, h)
    full_bleed = old.getpixel((0, 0))[3] > 200 and old.getpixel((w - 1, 0))[3] > 200
    new = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if full_bleed:
        new.paste(tile(w, False).resize((w, h)), (0, 0))
    else:
        bw = bbox[2] - bbox[0]
        t = tile(bw, True)
        new.paste(t, (bbox[0], bbox[1]), t)
    box = badge_box(old)
    if box:
        new.paste(old.crop(box), box[:2])
    if old.mode != "RGBA" and Image.open(path).mode == "RGB":
        new = new.convert("RGB")
    new.save(path, optimize=True)
    print(f"{path}: {'full-bleed' if full_bleed else 'rounded'}{' +badge' if box else ''}")


def png_bytes(img):
    b = io.BytesIO(); img.save(b, "PNG", optimize=True); return b.getvalue()


def main():
    os.chdir(ROOT)
    pngs = []
    for d in ["Assets.xcassets/AppIcon.appiconset", "Assets.xcassets/AppIcon-Debug.appiconset",
              "Assets.xcassets/AppIcon-Nightly.appiconset", "Assets.xcassets/AppIcon-RC.appiconset",
              "ios/cmux/Assets.xcassets/AppIcon.appiconset", "ios/cmux/Assets.xcassets/AppIcon-Demo.appiconset",
              "ios/cmux/Assets.xcassets/CmuxLogo.imageset", "ios/cmux/Assets.xcassets/LaunchLogo.imageset",
              "web/public/brand"]:
        pngs += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".png")]
    pngs += ["Assets.xcassets/AppIconDark.imageset/AppIconDark.png", "Assets.xcassets/AppIconLight.imageset/AppIconLight.png",
             "Assets.xcassets/CloudWelcomeIcon.imageset/CloudWelcomeIcon.png",
             "web/public/logo.png", "web/public/logo-nightly.png", "web/app/apple-icon.png", "web/app/icon.png"]
    for p in pngs:
        replace_png(p)

    # Icon Composer documents: one full-bleed layer; the system applies the icon shape.
    for icon_dir in ["AppIcon.icon", "design/cmux.icon"]:
        assets = os.path.join(icon_dir, "Assets")
        for f in os.listdir(assets):
            os.remove(os.path.join(assets, f))
        tile(1024, False).save(os.path.join(assets, "chadmux-icon.png"), optimize=True)
        meta_path = os.path.join(icon_dir, "icon.json")
        meta = json.load(open(meta_path))
        layer = meta["groups"][0]["layers"][0]
        layer["image-name"] = "chadmux-icon.png"
        layer["name"] = "chadmux-icon"
        layer["position"] = {"scale": 1, "translation-in-points": [0, 0]}
        meta["groups"][0]["translucency"] = {"enabled": False, "value": 0.5}
        with open(meta_path, "w") as fh:
            json.dump(meta, fh, indent=2, separators=(",", " : "))
            fh.write("\n")
        print(f"{icon_dir}: replaced layer")
    if os.path.exists("design/cmux-icon-chevron.png"):
        os.remove("design/cmux-icon-chevron.png")
    tile(1024, True).save("design/chadmux/chadmux-icon-rounded-1024.png", optimize=True)

    # favicon.ico
    tile(256, True).save("web/app/favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])

    # SVG marks: embed the rounded raster.
    data = base64.b64encode(png_bytes(tile(256, True))).decode()
    with open("web/public/cmux-icon.svg", "w") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 256 256" role="img" aria-labelledby="title">\n'
                 '  <title id="title">chadmux icon</title>\n'
                 f'  <image width="256" height="256" href="data:image/png;base64,{data}" />\n</svg>\n')

    # iOS sign-in mark: swap the vector chevron for PNG renditions (asset name unchanged).
    mark = "ios/cmux/Assets.xcassets/CmuxSignInMark.imageset"
    os.remove(os.path.join(mark, "cmux-sign-in-mark.svg"))
    images = []
    for scale in (1, 2, 3):
        name = "cmux-sign-in-mark.png" if scale == 1 else f"cmux-sign-in-mark@{scale}x.png"
        tile(64 * scale, True).save(os.path.join(mark, name), optimize=True)
        images.append({"filename": name, "idiom": "universal", "scale": f"{scale}x"})
    with open(os.path.join(mark, "Contents.json"), "w") as fh:
        json.dump({"images": images, "info": {"author": "xcode", "version": 1}}, fh, indent=2, separators=(",", " : "))
        fh.write("\n")
    print("done")


if __name__ == "__main__":
    main()
