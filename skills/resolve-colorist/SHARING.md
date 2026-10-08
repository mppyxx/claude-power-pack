# Sharing resolve-colorist

## The short version

You are free to use this skill, change it and pass it on to friends. There is no formal licence and no
warranty: it is a hobby tool shared between people who grade their own footage. Check your grade with your own
eyes before you deliver it to anyone, and keep the backup the skill makes before it touches a timeline.

## What is and is not inside

- All the code in this folder was written for this skill. It contains no code, LUTs or files from Blackmagic
  Design. DaVinci Resolve, its scripting API and its AI assistant server belong to Blackmagic Design and are
  used as they ship with Resolve Studio.
- The camera log curves and color gamut matrices in `cameras.py` are written from the camera makers' own public
  documents and from the open standards listed below. They were checked against the open source
  colour-science library where it has the same formula.
- DJI D-Log M and GoPro GP-Log are not included. Their makers have not published the formulas, and the
  community approximations that exist carry no licence. For those cameras, use the maker's own conversion LUT
  (see README.md, "Supported cameras").
- Apart from the install script asking pip to download numpy and pillow, nothing in this folder talks to the
  internet. Your footage, frames and grades stay on your computer.

## Credits for the formulas

- Sony: S-Log3 / S-Gamut3 / S-Gamut3.Cine technical summary; S-Log2 technical paper.
- Apple: Apple Log profile; Apple Log 2 white paper.
- DJI: D-Log / D-Gamut white papers; the D-Log2 / D-Gamut2 conversion.
- Panasonic: V-Log / V-Gamut reference manual.
- Canon: Canon Log gamma curves white paper; Canon Log transfer characteristic.
- Fujifilm: F-Log, F-Log2 and F-Log2 C data sheets.
- Nikon: N-Log specification.
- ARRI: LogC3 and LogC4 specifications.
- Blackmagic Design: Generation 5 color science reference; DaVinci Wide Gamut / DaVinci Intermediate
  information note.
- RED: white paper on REDWideGamutRGB and Log3G10.
- GoPro: GP-Log2 (GoPro Labs documentation); legacy Protune curve as published in the OpenColorIO ACES configs.
- Samsung: Samsung Log developer page. Insta360: I-Log white paper. Leica: L-Log reference manual.
- Standards: ITU-R BT.709, BT.1886, BT.2020, BT.2100, BT.2408; SMPTE ST 2084; IEC 61966-2-1 (sRGB).
- Methods: the grayness index for white balance (Qian et al., 2019); the reference gamut compression curve from
  the ACES project; a widely used filmic curve fit (Narkowicz); skin hue data from Wang, Xiao, Wuerger et al.
  (CIC 2015).

## How to share it

Share the folder without the `.venv` and `__pycache__` folders (they are machine specific and large). Your
friend runs the install script on their own computer, which rebuilds `.venv`.

macOS or Linux, from the folder that contains `resolve-colorist`:

    zip -r resolve-colorist.zip resolve-colorist -x "resolve-colorist/.venv/*" "*/__pycache__/*" "*.pyc" "*/.DS_Store"

Windows PowerShell, from the folder that contains `resolve-colorist`:

    $src = "resolve-colorist"; $tmp = Join-Path $env:TEMP "resolve-colorist-share"
    robocopy $src (Join-Path $tmp "resolve-colorist") /E /XD .venv __pycache__ /XF *.pyc
    Compress-Archive -Path (Join-Path $tmp "resolve-colorist") -DestinationPath resolve-colorist.zip -Force
    Remove-Item -Recurse -Force $tmp

Your friend unzips it into `~/.claude/skills/` (Windows: `%USERPROFILE%\.claude\skills\`) so that
`~/.claude/skills/resolve-colorist/SKILL.md` exists, and follows README.md from "Install". Windows "Extract All"
proposes a folder named after the zip and so adds a second `resolve-colorist` level; tell your friend to extract
into the `skills` folder itself, or to move the inner folder up afterwards. The installer refuses to run from the
wrong place and says how to fix it.

Before you share, make sure no lab folders, renders or `timeline.json` files ended up inside the skill folder:
they contain your project's clip names and file paths.
