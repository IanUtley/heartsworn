"""Build client asset bundles holding a custom card set's art, as the Hex client loads them.

How the client finds a card's portrait (Assembly-CSharp/GoCardBuilder.cs, AssetBundleManager.cs):

* It takes the file name of the card's ``m_CardImagePath`` (no folders, no extension) and
  loads ``AssetBundles/cardsets/<set m_BundleName, lower case>/<name, lower case>.ab``,
  then the Texture2D named ``<name>`` inside it. A loose ``.ab`` file is used before the
  set's ``.harc`` archive, so a new set needs only a folder of bundles.
* Small card views first try the set's ``Atlas`` bundle; a set without one falls back to
  the full texture (``OnLoadAtlasTexture`` -> "Trying full texture"), so no atlas is built.
* The texture is 512x512; the card's ``m_DefaultLayout`` crops it. Records cards mostly
  show x 0.07-0.92, y 0.0139-0.7434 from the top (``LAYOUT``), so each art is fitted to
  that window over a blurred fill of itself. Cards sent to the client should carry
  ``LAYOUT`` as their ``m_DefaultLayout``.
* Records' bundles: Unity 5.6.5p2, BuildTarget StandaloneWindows, chunk-based (LZ4)
  compression, one DXT1 texture without mipmaps, bundle ``cardsets/<set>/<art>.ab``
  (name + variant "ab"), asset ``assets/assetbundlecontent/cardsets/<set>/<art>/<art>.png``.

Two builders make the same bundles:

* default: UnityPy, from one of the client's own card bundles as the template (read from
  ``<client>/AssetBundles/cardsets/set001.harc``). Each copy gets the art (DXT1, 1 mip),
  its own names, a container of just that texture (the template's card-play sound is
  dropped) and the internal file name Unity gives that bundle, ``CAB-`` + MD5 of its
  name, so two copies never clash when the client loads both.
* ``--unity``: the Unity 5.6.5p2 editor in batch mode (it needs an activated licence).

Usage (UnityPy must be importable, e.g. from a venv)::

    python3 AssetExtraction/build_custom_art_bundles.py [--out client_patch] [--limit N] [--unity]

Reads ``custom_sets/CardSetTemplate.json`` and ``custom_sets/CardTemplates.json``; writes
``<out>/AssetBundles/cardsets/<set>/*.ab``, laid out like the client folder, to be copied
over the Hex install by the player. The game folder is only read. Needs Pillow; UnityPy
for the default builder; for ``--unity``, the editor (``UNITY_EXE``) and a Windows temp
folder (``UNITY_PROJECT``) for the scratch project (WSL paths go through ``wslpath``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
CUSTOM = ROOT / "custom_sets"
UNITY_EXE = Path("/mnt/c/Program Files/Unity/Hub/Editor/5.6.5p2/Editor/Unity.exe")
UNITY_PROJECT = Path("/mnt/c/Users/utley/AppData/Local/Temp/HexArtBundles")
CLIENT = Path("/mnt/d/SteamLibrary/steamapps/common/HEX SHARDS OF FATE")
TEMPLATE = ("AssetBundles/cardsets/set001.harc", "./a0000001.ab")   # a Records card's art bundle
DXT1 = 10   # TextureFormat.DXT1, as the client's own card art
SIZE = 512
# The portrait window most Records cards use (m_DefaultLayout), as fractions from the top left.
LAYOUT = {"m_PortraitLeft": 0.07, "m_PortraitRight": 0.92,
          "m_PortraitTop": 0.0138689652, "m_PortraitBottom": 0.74339366}

EDITOR_SCRIPT = r"""
using System.IO;
using UnityEditor;
using UnityEngine;

// Builds every PNG under Assets/AssetBundleContent/cardsets/<set>/<art>/<art>.png into
// the bundle cardsets/<set>/<art>.ab, the way the Hex client's own card art is built.
public static class HexArtBuild
{
    public static void Build()
    {
        AssetDatabase.Refresh();
        string root = "Assets/AssetBundleContent/cardsets";
        foreach (string file in Directory.GetFiles(root, "*.png", SearchOption.AllDirectories))
        {
            string path = file.Replace('\\', '/');
            string art = Path.GetFileNameWithoutExtension(path);
            string set = Path.GetFileName(Path.GetDirectoryName(Path.GetDirectoryName(path)));
            TextureImporter importer = (TextureImporter)AssetImporter.GetAtPath(path);
            importer.textureType = TextureImporterType.Default;
            importer.mipmapEnabled = false;
            importer.isReadable = false;
            importer.alphaSource = TextureImporterAlphaSource.None;
            importer.npotScale = TextureImporterNPOTScale.None;
            importer.maxTextureSize = 512;
            importer.textureCompression = TextureImporterCompression.Compressed;
            TextureImporterPlatformSettings standalone = importer.GetPlatformTextureSettings("Standalone");
            standalone.overridden = true;
            standalone.maxTextureSize = 512;
            standalone.format = TextureImporterFormat.DXT1;
            importer.SetPlatformTextureSettings(standalone);
            importer.SetAssetBundleNameAndVariant("cardsets/" + set + "/" + art, "ab");
            importer.SaveAndReimport();
        }
        Directory.CreateDirectory("Bundles");
        BuildPipeline.BuildAssetBundles("Bundles", BuildAssetBundleOptions.ChunkBasedCompression,
                                        BuildTarget.StandaloneWindows);
    }
}
"""


def compose(source: Path) -> Image.Image:
    """The 512x512 texture: the art filling the portrait window over a blurred fill."""
    art = Image.open(source).convert("RGB")
    canvas = _cover(art, SIZE, SIZE).filter(ImageFilter.GaussianBlur(12))
    left, right = round(LAYOUT["m_PortraitLeft"] * SIZE), round(LAYOUT["m_PortraitRight"] * SIZE)
    top, bottom = round(LAYOUT["m_PortraitTop"] * SIZE), round(LAYOUT["m_PortraitBottom"] * SIZE)
    canvas.paste(_cover(art, right - left, bottom - top), (left, top))
    return canvas


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """``image`` scaled to cover width x height and cropped to it (centre, upper third
    for tall art so faces stay in view)."""
    scale = max(width / image.width, height / image.height)
    resized = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))),
                           Image.LANCZOS)
    x = (resized.width - width) // 2
    y = (resized.height - height) // 3 if resized.height > resized.width else (resized.height - height) // 2
    return resized.crop((x, y, x + width, y + height))


def windows(path: Path) -> str:
    return subprocess.run(["wslpath", "-w", str(path)], check=True, capture_output=True, text=True).stdout.strip()


def harc_item(path: Path, name: str) -> bytes:
    """One file of a client ``.harc`` archive (Game/Shared/Utils/Harc.cs): a ``HARC``
    line, ``offset,size,name`` lines, a blank line, then the files."""
    with path.open("rb") as stream:
        head = stream.read(1 << 20)
        if not head.startswith(b"HARC\r\n"):
            raise ValueError(f"{path} is not a HARC archive")
        end = head.index(b"\r\n\r\n", 6)
        for line in head[6:end].decode("utf-8").split("\r\n"):
            offset, size, item = line.split(",", 2)
            if item == name:
                stream.seek(end + 4 + int(offset))
                return stream.read(int(size))
    raise KeyError(f"{name} is not in {path}")


def unitypy_bundle(template: bytes, card_set: str, art: str, texture: Image.Image) -> bytes:
    """A card art bundle built from a client bundle: the texture renamed and replaced,
    the container holding only it, and the bundle's own names."""
    import UnityPy

    env = UnityPy.load(template)
    bundle = env.file
    name = f"cardsets/{card_set}/{art}.ab"
    cab = "CAB-" + hashlib.md5(name.encode("utf-8")).hexdigest()   # as Unity names it
    serialized_name = next(key for key in bundle.files if not key.endswith(".resource"))
    serialized = bundle.files[serialized_name]
    texture_object = next(o for o in serialized.objects.values() if o.type.name == "Texture2D")
    for path_id in [p for p, o in serialized.objects.items() if o.type.name not in ("Texture2D", "AssetBundle")]:
        del serialized.objects[path_id]          # the template card's play sound
    tex = texture_object.read()
    tex.m_Name = art
    tex.set_image(texture, target_format=DXT1, mipmap_count=1)
    tex.save()
    manifest_object = next(o for o in serialized.objects.values() if o.type.name == "AssetBundle")
    manifest = manifest_object.read_typetree()
    pointer = {"m_FileID": 0, "m_PathID": texture_object.path_id}
    manifest.update(m_Name=name, m_AssetBundleName=name, m_PreloadTable=[pointer], m_Container=[[
        f"assets/assetbundlecontent/cardsets/{card_set}/{art}/{art}.png",
        {"preloadIndex": 0, "preloadSize": 1, "asset": pointer}]])
    manifest_object.save_typetree(manifest)
    bundle.files = {cab: serialized}         # the sound's .resource file goes with it
    serialized.name = cab
    return bundle.save(packer="original")


def unity_build(images: dict[tuple[str, str], Image.Image]) -> dict[tuple[str, str], bytes]:
    """The same bundles from the Unity editor in batch mode (needs an activated licence)."""
    content = UNITY_PROJECT / "Assets" / "AssetBundleContent" / "cardsets"
    if content.exists():
        shutil.rmtree(content)
    for (card_set, art), image in images.items():
        folder = content / card_set / art
        folder.mkdir(parents=True, exist_ok=True)
        image.save(folder / f"{art}.png")
    editor = UNITY_PROJECT / "Assets" / "Editor"
    editor.mkdir(parents=True, exist_ok=True)
    (editor / "HexArtBuild.cs").write_text(EDITOR_SCRIPT, encoding="utf-8")
    log = UNITY_PROJECT / "build.log"
    result = subprocess.run([str(UNITY_EXE), "-batchmode", "-quit", "-nographics",
                             "-projectPath", windows(UNITY_PROJECT),
                             "-executeMethod", "HexArtBuild.Build", "-logFile", windows(log)])
    if result.returncode != 0:
        raise RuntimeError(f"Unity failed ({result.returncode}); see {log}")
    return {key: (UNITY_PROJECT / "Bundles" / "cardsets" / key[0] / f"{key[1]}.ab").read_bytes()
            for key in images}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "client_patch")
    parser.add_argument("--client", type=Path, default=CLIENT, help="the Hex install (read only)")
    parser.add_argument("--limit", type=int, default=0, help="build only the first N cards (a trial)")
    parser.add_argument("--unity", action="store_true", help="build with the Unity editor instead of UnityPy")
    args = parser.parse_args()

    sets = {entry["m_Id"]["m_Guid"].lower(): entry for entry in
            json.loads((CUSTOM / "CardSetTemplate.json").read_text(encoding="utf-8"))}
    cards = json.loads((CUSTOM / "CardTemplates.json").read_text(encoding="utf-8"))
    images: dict[tuple[str, str], Image.Image] = {}
    for card in cards[:args.limit or None]:
        image = str(card.get("m_CardImagePath") or "")
        card_set = sets.get(str(card.get("m_SetId", {}).get("m_Guid") or "").lower())
        if not image or not card_set:
            continue
        source = ROOT / image
        # The client lower-cases the bundle path and looks the texture up by file name.
        images[(str(card_set["m_BundleName"]).lower(), source.stem.lower())] = compose(source)
    print(f"Composed {len(images)} textures", flush=True)
    if args.unity:
        bundles = unity_build(images)
    else:
        template = harc_item(args.client / TEMPLATE[0], TEMPLATE[1])
        bundles = {key: unitypy_bundle(template, key[0], key[1], image) for key, image in images.items()}
    for (card_set, art), data in sorted(bundles.items()):
        target = args.out / "AssetBundles" / "cardsets" / card_set
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{art}.ab").write_bytes(data)
    for card_set in sorted({key[0] for key in bundles}):
        count = sum(1 for key in bundles if key[0] == card_set)
        print(f"{count} bundles -> {args.out / 'AssetBundles' / 'cardsets' / card_set}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
