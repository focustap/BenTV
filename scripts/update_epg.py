#!/usr/bin/env python3
import gzip
import io
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE = "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"
ERSATZ_SOURCE = "https://desktop-9j9r86p.tailc28749.ts.net/iptv/xmltv.xml"
ERSATZ_CHANNEL_ID = "c80.200.ersatztv.org"
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


def episode_label(element):
    for child in element.findall("episode-num"):
        if child.attrib.get("system") == "onscreen" and child.text:
            return child.text.strip()
    return ""


def fetch_bytes(url, timeout=90):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BenTV-Guide/1.1 (+https://github.com/focustap/BenTV)"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_epgshare(programmes, window_start, window_end):
    compressed = fetch_bytes(SOURCE)

    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as xml_stream:
        for _event, elem in ET.iterparse(xml_stream, events=("end",)):
            if elem.tag != "programme":
                continue

            channel_id = elem.attrib.get("channel", "")
            key = ID_TO_KEY.get(channel_id)

            if key:
                start = parse_xmltv_time(elem.attrib.get("start", ""))
                stop = parse_xmltv_time(elem.attrib.get("stop", ""))

                if start and stop and stop > start and stop >= window_start and start <= window_end:
                    programmes[key].append({
                        "title": text_of(elem, "title") or "Untitled",
                        "subtitle": text_of(elem, "sub-title"),
                        "start": start.isoformat().replace("+00:00", "Z"),
                        "stop": stop.isoformat().replace("+00:00", "Z"),
                    })

            elem.clear()


def parse_ersatztv(programmes, window_start, window_end):
    xml_data = fetch_bytes(ERSATZ_SOURCE, timeout=30)
    root = ET.fromstring(xml_data)

    channel_ids = set()
    for channel in root.findall("channel"):
        display_names = [
            (node.text or "").strip().lower()
            for node in channel.findall("display-name")
        ]
        channel_id = channel.attrib.get("id", "")

        if "bencentral" in display_names or channel_id == ERSATZ_CHANNEL_ID:
            channel_ids.add(channel_id)

    if not channel_ids:
        channel_ids.add(ERSATZ_CHANNEL_ID)

    seen_programme_channels = set()

    for elem in root.findall("programme"):
        channel_id = elem.attrib.get("channel", "")
        seen_programme_channels.add(channel_id)

        if channel_id not in channel_ids:
            continue

        start = parse_xmltv_time(elem.attrib.get("start", ""))
        stop = parse_xmltv_time(elem.attrib.get("stop", ""))

        if not start or not stop or stop <= start:
            continue

        # Keep the full ErsatzTV playout returned by XMLTV. The BenTV frontend
        # already clips listings to the visible window, and retaining the whole
        # feed avoids dropping valid items when the playout crosses timezones.
        title = text_of(elem, "title") or "BenCentral"
        episode = episode_label(elem)
        subtitle = episode or text_of(elem, "sub-title")

        programmes["bencentral"].append({
            "title": title,
            "subtitle": subtitle,
            "start": start.isoformat().replace("+00:00", "Z"),
            "stop": stop.isoformat().replace("+00:00", "Z"),
        })

    print("ErsatzTV BenCentral channel IDs:", ", ".join(sorted(channel_ids)))
    print("ErsatzTV programme channel IDs:", ", ".join(sorted(seen_programme_channels)))

def restore_previous_bencentral(programmes, window_start, window_end):
    if not OUTPUT.exists():
        return

    try:
        previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except Exception:
        return

    for item in previous.get("channels", {}).get("bencentral", []):
        start = parse_xmltv_time(item.get("start", "").replace("-", "").replace(":", "").replace("T", "").replace("Z", ""))
        stop = parse_xmltv_time(item.get("stop", "").replace("-", "").replace(":", "").replace("T", "").replace("Z", ""))

        # The previous guide uses ISO timestamps, so parse them directly if the XMLTV parser did not.
        try:
            start = datetime.fromisoformat(item["start"].replace("Z", "+00:00")).astimezone(timezone.utc)
            stop = datetime.fromisoformat(item["stop"].replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue

        if stop > start and stop >= window_start and start <= window_end:
            programmes["bencentral"].append(item)


def main():
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=3)
    window_end = now + timedelta(hours=24)

    programmes = {key: [] for key in TARGET_IDS}
    programmes["bencentral"] = []

    parse_epgshare(programmes, window_start, window_end)

    try:
        parse_ersatztv(programmes, window_start, window_end)
    except Exception as exc:
        print(f"Warning: BenCentral guide fetch failed: {exc}")
        restore_previous_bencentral(programmes, window_start, window_end)

    for key in programmes:
        programmes[key].sort(key=lambda item: item["start"])

    missing = [key for key in TARGET_IDS if not programmes[key]]
    if len(missing) == len(TARGET_IDS):
        raise RuntimeError("No target channels were found in the EPG feed.")

    payload = {
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "source": "EPGShare01 US2 + ErsatzTV",
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
