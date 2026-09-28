#!/usr/bin/env python3
import gzip
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE = "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"
OUTPUT = Path(__file__).resolve().parents[1] / "guide.json"

TARGET_IDS = {
    "freeform": "Freeform.HD.us2",
    "disney": "Disney.Channel.HD.us2",
    "nickelodeon": "Nickelodeon.HD.us2",
    "nicktoons": "Nicktoons.us2",
    "teenick": "Teen.Nick.us2",
    "trutv": "truTV.HD.us2",
    "cartoonnetwork": "Cartoon.Network.HD.us2",
    "disneyxd": "Disney.XD.HD.us2",
}

ID_TO_KEY = {channel_id: key for key, channel_id in TARGET_IDS.items()}


def parse_xmltv_time(value: str):
    if not value:
        return None

    match = re.match(r"^(\d{14})(?:\s*([+-]\d{4}))?", value.strip())
    if not match:
        return None

    stamp, offset = match.groups()
    if offset:
        dt = datetime.strptime(stamp + offset, "%Y%m%d%H%M%S%z")
    else:
        dt = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def text_of(element, tag):
    child = element.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def main():
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=3)
    window_end = now + timedelta(hours=24)

    request = urllib.request.Request(
        SOURCE,
        headers={"User-Agent": "BenTV-Guide/1.0 (+https://github.com/focustap/BenTV)"}
    )

    with urllib.request.urlopen(request, timeout=90) as response:
        compressed = response.read()

    programmes = {key: [] for key in TARGET_IDS}

    with gzip.GzipFile(fileobj=__import__("io").BytesIO(compressed)) as xml_stream:
        for event, elem in ET.iterparse(xml_stream, events=("end",)):
            if elem.tag == "programme":
                channel_id = elem.attrib.get("channel", "")
                key = ID_TO_KEY.get(channel_id)

                if key:
                    start = parse_xmltv_time(elem.attrib.get("start", ""))
                    stop = parse_xmltv_time(elem.attrib.get("stop", ""))

                    if start and stop and stop >= window_start and start <= window_end:
                        title = text_of(elem, "title") or "Untitled"
                        subtitle = text_of(elem, "sub-title")

                        programmes[key].append({
                            "title": title,
                            "subtitle": subtitle,
                            "start": start.isoformat().replace("+00:00", "Z"),
                            "stop": stop.isoformat().replace("+00:00", "Z"),
                        })

                elem.clear()

    for key in programmes:
        programmes[key].sort(key=lambda item: item["start"])

    missing = [key for key in TARGET_IDS if not programmes[key]]
    if len(missing) == len(TARGET_IDS):
        raise RuntimeError("No target channels were found in the EPG feed.")

    payload = {
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "source": "EPGShare01 US2",
        "channels": programmes,
    }

    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("Guide updated.")
    for key, items in programmes.items():
        print(f"  {key}: {len(items)} programmes")
    if missing:
        print("Missing schedule data for:", ", ".join(missing))


if __name__ == "__main__":
    main()
