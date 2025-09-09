# -*- coding: utf-8 -*-
"""
Created on Tue Mar 28 17:32:38 2017

@author: tangible
"""

import os
from os.path import expanduser
import analyses
from tangible_utils import get_environment  # (unused here, but harmless)
from blender import blender_export_PNG, blender_send_file
from activities import updateDisplay
import grass.script as gscript
from grass.exceptions import CalledModuleError
from pathlib import Path
import grass.jupyter as gj

import shutil

trees = {1: "class1", 2: "class2", 3: "class3", 4: "class4"}

# --- helpers --------------------------------------------------------------


def _grass_major():
    try:
        return gscript.version()["version"].split(".")[0]  # "8" for 8.4.0
    except Exception:
        return "8"


def _raster_exists(name, env):
    return bool(gscript.find_file(name=name, element="cell", env=env).get("name"))


# --- main workflow --------------------------------------------------------


def run_patches(
    real_elev, scanned_elev, scanned_color, blender_path, eventHandler, env, **kwargs
):
    topo = "topo_saved"

    # 1) detect patches (cloth colors -> categories)
    patches = "patches"
    analyses.classify_colors(
        new=patches,
        group=scanned_color,
        compactness=2,
        threshold=0.3,
        minsize=10,
        useSuperPixels=True,
        env=env,
    )
    gscript.run_command(
        "r.to.vect", flags="svt", input=patches, output=patches, type="area", env=env
    )

    base_cat = [7]  # categories to ignore (mixed, etc.)

    # r.li setup (GRASS 8.x path)
    indices_prefix = "index_"
    rliroot = os.path.join(expanduser("~"), f".grass{_grass_major()}", "r.li")
    configpath = os.path.join(rliroot, "patches")
    outputpath = os.path.join(rliroot, "output")
    os.makedirs(rliroot, exist_ok=True)
    os.makedirs(outputpath, exist_ok=True)
    if not os.path.exists(configpath):
        with open(configpath, "w") as f:
            f.write("SAMPLINGFRAME 0|0|1|1\n")
            f.write("SAMPLEAREA 0.0|0.0|1|1\n")

    # raster without base_cat values
    gscript.mapcalc(
        "{p2} = if({p} != {cl1}, int({p}), null())".format(
            p2=patches + "2", p=patches, cl1=base_cat[0]
        ),
        env=env,
    )
    gscript.run_command("g.region", raster=patches + "2", env=env)

    # 2) landscape indices (robust against empty inputs)
    results_list = []
    rliindices = ["patchnum", "richness", "mps", "shannon", "shape"]

    univar = gscript.parse_command("r.univar", map=patches + "2", flags="g", env=env)
    has_cells = bool(univar and float(univar.get("n", 0)) > 0)

    if not has_cells:
        # zeros if there are no patch pixels
        results_list = [0.0 for _ in rliindices]
    else:
        for index in rliindices:
            try:
                gscript.run_command(
                    "r.li." + index,
                    input=patches + "2",
                    output=indices_prefix + index,
                    config=configpath,
                    env=env,
                )
                with open(os.path.join(outputpath, indices_prefix + index), "r") as f:
                    r = f.readlines()[0].strip().split("|")[-1]
                val = float(r)
                if index == "mps":
                    val *= 10  # keep original scaling
                results_list.append(val)
            except CalledModuleError:
                # if any r.li fails, return zeros (but keep pipeline alive)
                results_list = [0.0 for _ in rliindices]
                break

    # 3) remediation percentage (only if waterall exists)
    if _raster_exists("waterall", env):
        gscript.run_command(
            "r.grow",
            flags="m",
            input="waterall",
            output="waterallg",
            radius=30,
            new=1,
            env=env,
        )
        gscript.mapcalc(
            "{new} = if({w} && {p} == 5, 1, null())".format(
                new="remed", w="waterallg", p=patches + "2"
            ),
            env=env,
        )
        u = gscript.parse_command("r.univar", map="remed", flags="g", env=env)
        remed_size = float(u.get("n", 0)) if u else 0.0
        u = gscript.parse_command("r.univar", map="waterall", flags="g", env=env)
        waterall_n = float(u.get("n", 0)) if u else 0.0
        perc = 100.0 * remed_size / waterall_n if waterall_n else 0.0
    else:
        perc = 0.0
    results_list.insert(0, perc)  # prepend remediation %

    # 4) update dashboard (if TL UI is running)
    event = updateDisplay(value=results_list)
    eventHandler.postEvent(receiver=eventHandler.activities_panel, event=event)

    # 5) export patches to Blender
    gscript.mapcalc("scanned_scan_int = int({})".format(scanned_elev), env=env)

    # vector styling (color rules): prefer your fixed file; else training_areas; else local file
    patch_rast = patches + "2"  # source raster (same as your patch_vec_src)
    patch_vec_raw = patches + "2_vec"
    patch_vec = patches + "2gen"  # smoothed vector you’ll color/display
    try:
        # convert & smooth (safe on re-runs)
        gscript.run_command(
            "r.to.vect",
            input=patch_rast,
            output=patch_vec_raw,
            type="area",
            env=env,
            overwrite=True,
        )  # <-- no '-t'

        gscript.run_command(
            "v.generalize",
            input=patch_vec_raw,
            type="area",
            output=patch_vec,
            method="snakes",
            threshold=100,
            env=env,
            overwrite=True,
        )

        # color rules (use your file first)
        rules_path = Path("/home/buas/Documents/TL_Activities/patch_colors.txt")
        if rules_path.exists():
            gscript.run_command(
                "v.colors", map=patch_vec, rules=str(rules_path), env=env
            )
        elif _raster_exists("training_areas", env):
            rules_txt = gscript.read_command(
                "r.colors.out", map="training_areas", env=env
            )
            rules_txt = "\n".join(
                ln
                for ln in rules_txt.splitlines()
                if ln and not ln.startswith(("nv", "default"))
            )
            if rules_txt:
                gscript.write_command(
                    "v.colors", map=patch_vec, rules="-", stdin=rules_txt, env=env
                )
            # 3) fallback: file next to this script
            else:
                alt = Path(__file__).with_name("patch_colors.txt")
                if alt.exists():
                    gscript.run_command(
                        "v.colors", map=patch_vec, rules=str(alt), env=env
                    )

    except CalledModuleError:
        # styling is optional; keep going even if it fails
        pass

    # clear any MASK (best effort)
    try:
        gscript.run_command("r.mask", flags="r", env=env)
    except Exception:
        pass

    gscript.run_command("g.region", raster=topo, align=topo, env=env)

    # 6) per-class masks for Blender (black=plant, white=don’t)
    cats_raw = gscript.read_command(
        "r.describe", flags="1ni", map=patches, env=env
    ).strip()
    cats = [int(cat) for cat in cats_raw.splitlines()] if cats_raw else []
    toexport = []

    # mask export settings
    USE_SUBTRACT = kwargs.get(
        "use_subtract", True
    )  # only True if Blender is set to SUBTRACT
    BW_RULES = kwargs.get("bw_rules", "/tmp/mask_bw.rules")
    if not os.path.exists(BW_RULES):
        with open(BW_RULES, "w") as f:
            f.write("0 0:0:0\n1 255:255:255\n")  # 0 -> black, 1 -> white

    # build one mask per class we care about
    for cat in cats:
        if cat in base_cat:
            continue
        name = trees.get(cat)
        if not name:
            continue

        mask = f"patch_{name}"
        if USE_SUBTRACT:
            # black inside (plant), white outside (don’t) -> for SUBTRACT
            expr = f"{mask}=if(isnull({patches}),1.0, if({patches}=={cat},0.0,1.0))"
        else:
            # white inside (plant), black outside (don’t) -> default Mix/Multiply
            expr = f"{mask}=if(isnull({patches}),0.0, if({patches}=={cat},1.0,0.0))"

        gscript.mapcalc(expr, env=env, overwrite=True)
        gscript.run_command("r.colors", map=mask, rules=BW_RULES, env=env)
        toexport.append(mask)

    # --- export masks as PNGs and drop them into Watch/ ---
    root = Path(blender_path)
    watch = root / "Watch"
    watch.mkdir(parents=True, exist_ok=True)

    # lock export region so PNGs match Blender plane
    for png in toexport:
        out = root / f"{png}.png"
        try:
            gscript.run_command(
                "r.out.gdal",
                input=png,
                output=str(out),
                format="PNG",
                type="Byte",
                createopt="ZLEVEL=1,INTERLACE=0",
                flags="c",  # avoid extra sidecars when possible
                env=env,
                overwrite=True,
            )
        except CalledModuleError:
            # fallback if GDAL export not available
            img = gj.Map(use_region=True, width=2048)
            img.d_rast(map=png)
            img.save(out)

        dest = watch / out.name
        shutil.copyfile(out, dest)

        # clean sidecars Blender doesn’t need
        for ext in (".aux.xml", ".prj", ".wld", ".tfw", ".pgw"):
            p = Path(str(dest) + ext)
            if p.exists():
                p.unlink()
