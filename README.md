# Infinity Core for Home Assistant

This integration shows each Infinity Core zone as a media player in Home Assistant. A zone is one profile's music:
what it plays, its queue and the output it plays on. You can see what plays and control it from Home Assistant, its
dashboards and its automations.

It connects to Infinity Core as an extension, over your own network. Infinity Core's Owner switches it on once in
Infinity Core › Settings › Extensions.

**Needs** an Infinity Core that has Settings › Extensions. An older one is refused when you add it ("too old");
update Infinity Core first.

## What you get

One media player per zone that Home Assistant may see, all under one device named after your Infinity Core. Each
player is named after its zone. A zone that appears later (for example, a new profile) gets its own player when
Infinity Core first reports it.

The player shows:

- the state: playing, paused, buffering (Infinity Core is loading) or idle (stopped);
- the title, the artists, the album, the length and the position;
- the cover;
- the volume and mute of the zone's output;
- shuffle and repeat;
- the source: the output the zone plays on.

During an Infinity Core private session the player shows only its state, output, volume, shuffle and repeat: no
title, artist, album, length, cover or position.

The controls:

| in Home Assistant | in Infinity Core |
|---|---|
| Play, Pause, Stop | play, pause or stop the zone |
| Next, Previous | the next or previous song |
| Seek | jump to that place in the song |
| Volume | the volume of the zone's output, on that output's own scale (dB, percent or steps) |
| Volume up / down | the same, by Home Assistant's own step |
| Mute | mute or unmute that output |
| Shuffle | the zone's shuffle |
| Repeat | the zone's repeat: off, all or one |
| Source | move the music to another output; it goes on from the same place |

The source list holds every output Infinity Core lists: the computer's outputs, DAPs, network players. It follows
Infinity Core when outputs appear or go.

When the output's volume is fixed (a line out) or it has no volume, the player has no volume or mute controls.

It does not browse the library, start an album or a playlist, or change sound settings. Use Infinity Core for those.

## Install

You need Home Assistant 2024.8 or newer, and an Infinity Core that has Settings › Extensions.

### With HACS

1. In HACS, open the ⋮ menu › **Custom repositories**.
2. Enter `https://github.com/tekchansin/Infinity-Core-HomeAssistant`, choose the type **Integration**, and press
   **Add**.
3. Find **Infinity Core** in HACS and download it.
4. Restart Home Assistant.

### By hand

1. Download [github.com/tekchansin/Infinity-Core-HomeAssistant](https://github.com/tekchansin/Infinity-Core-HomeAssistant).
   Copy its folder `custom_components/infinity_core` into the `custom_components` folder of your Home Assistant
   configuration (create it if it is not there), so you have `config/custom_components/infinity_core/`.
2. Restart Home Assistant.

The integration needs nothing from the internet: the Python SDK of the
[Infinity Core API](https://github.com/tekchansin/Infinity-Core-API) is copied into its folder.

## Set up

### 1. Add Infinity Core to Home Assistant

**Found by itself.** Home Assistant finds Infinity Core on your network. Settings › Devices & services shows it under
**Discovered**. Press **Add**, then confirm **Add <name>?**.

**By address.** Settings › Devices & services › **Add integration** › **Infinity Core**. Enter:

- **Address**: the name or IP address of the computer or server that runs Infinity Core, for example `192.168.1.20`;
- **Port**: `8931`, unless your Infinity Core uses another.

Home Assistant checks the address, and the entry takes your Infinity Core's name. An Infinity Core too old for the
Infinity Core API is refused at this step (see [Troubleshooting](#troubleshooting)).

If the same Infinity Core is added twice, Home Assistant says "This Infinity Core is already added" and keeps one
entry. When it is found again at a new address, the entry follows it.

### 2. Enable it in Infinity Core

Until you do, Home Assistant shows a notification, **Infinity Core: enable Home Assistant**, and the players are
unavailable.

1. Open Infinity Core as its **Owner**: its own app or page, or the Mac, Windows or phone app connected to it. Not
   through Remote access.
2. Go to **Settings › Extensions**. **Home Assistant** is in the list as **Waiting for you**.
3. Press **Review**. It asks to:
   - see what the profile plays, has played and the library;
   - control the profile's music: play, pause, skip, volume, queue.
4. Choose **Acts as**: one profile, or **Everyone** (see below).
5. Press **Enable**.

The notification goes away and the players appear. Infinity Core gives Home Assistant a token, so this is needed only
once.

## Everyone or one profile

The choice in **Acts as** decides what Home Assistant sees and controls.

- **One profile**: one media player, for that profile's zone. Home Assistant controls only that profile's music. It
  cannot take an output another profile is using.
- **Everyone**: one media player per profile. Home Assistant sees and controls every profile's music, on every
  output, including music someone else is playing.

To change the choice later, press **Turn off** on the Home Assistant row in Settings › Extensions, then **Enable**
and choose again. A player for a zone Home Assistant may no longer see stays, but unavailable; delete it in Home
Assistant if you do not want it.

## Troubleshooting

### It is not offered automatically

Home Assistant finds Infinity Core by multicast DNS (`_infinity-core._tcp`). Discovery does not work when:

- Home Assistant and Infinity Core are on different networks or VLANs, or on a guest Wi-Fi;
- Home Assistant runs in Docker without host networking;
- the Infinity Core is too old for the Infinity Core API. It is then skipped. Update Infinity Core;
- you ignored it before. Look under **Ignored** in Settings › Devices & services.

You can always add it by address.

### "No Infinity Core answered at this address" (`cannot_connect`)

Home Assistant asks `https://<address>:<port>/lan/hello` and waits 10 seconds. Check that:

- the address is right, and Infinity Core is running there;
- the port is the one Infinity Core uses (8931 unless it is taken);
- a firewall on that computer lets Home Assistant reach the port.

### "This Infinity Core is too old for Home Assistant" (`not_supported`)

Infinity Core answered, but it has no Infinity Core API yet. Update Infinity Core, then try again.

### "Infinity Core: key changed"

Home Assistant checks Infinity Core's key on every connection. It keeps the key it was given when Infinity Core was
found, or the first one it saw. If the key is different, it stops connecting and shows this notification.

This is expected after you reinstalled Infinity Core, or moved it to another computer at the same address. Then
remove the integration, add it again, and enable it again in Infinity Core. If you changed nothing, do not: something
else may be answering at that address.

### The players stay unavailable after Enable, or the controls are refused

Home Assistant needs both permissions in the Review dialog: seeing what plays, and controlling it. If one was
unticked, press **Turn off** on the Home Assistant row in Settings › Extensions, then **Enable** with both ticked.
Without "see", no players appear; without "control", every control answers that it is not allowed.

### The volume cannot be changed

The zone's output has a fixed volume (a line out) or no volume control, so the player has no volume or mute
controls, and Infinity Core would refuse them. They come back when the zone moves to an output with a volume.

### A player is unavailable

A player is available only while Infinity Core has the integration enabled and the zone is one Home Assistant may
see. So it is unavailable when:

- the integration is not enabled yet (the notification is shown);
- it was turned off or removed in Settings › Extensions;
- Infinity Core is not running or cannot be reached. Home Assistant tries again by itself, after about 1, 2, 4 … up
  to 60 seconds;
- the key changed (see above);
- Infinity Core blocked the integration, or is too old for it. Home Assistant's log then says "Infinity Core will not
  work with Home Assistant: …";
- **Acts as** changed and Home Assistant may no longer see that zone.
- the profile it showed was deleted in Infinity Core: that zone's player stays, unavailable, and the integration
  stops until you press **Enable** again on its row and choose another profile (or Everyone).

### An error when choosing a source or playing

"That output is in use by another profile": another profile is playing on it. Choose another output, or enable the
integration as **Everyone**.

### Infinity Core asks to enable it again

- After you remove the integration and add it again, Home Assistant has a new token. Infinity Core may say "Home
  Assistant is already enabled from … This is another copy". Enable it if it is yours.
- After you press **Remove** in Settings › Extensions while Home Assistant still runs, it comes back as **Waiting for
  you**. To stop it, remove or disable the integration in Home Assistant.

### Logs

Add this to `configuration.yaml` to see more:

```yaml
logger:
  logs:
    custom_components.infinity_core: debug
```

## Privacy

- Home Assistant talks only to your Infinity Core, over your network, with TLS and Infinity Core's key checked.
  Nothing goes to us or to anyone else.
- What it sees, for the profile it acts as (or for every profile with **Everyone**): each zone's state, what plays
  (title, artists, album, length, position, cover), the output and its volume, shuffle and repeat, and the list of
  outputs.
- What it does not use: the `read` permission would also let it read the library, History and favourites. The
  integration never asks for them.
- Covers: Home Assistant asks Infinity Core for a cover address that works for 10 minutes, fetches the cover itself,
  and shows it through its own image proxy. Your browser does not talk to Infinity Core.
- Home Assistant's own history records the players' states and what they play, as for any media player, for as long
  as its recorder keeps data. During an Infinity Core private session the player shows only its state, output,
  volume, shuffle and repeat: no title, artist, album, length, cover or position, so none of it is recorded.
- The token and Infinity Core's key are kept in your Home Assistant configuration, in
  `.storage/infinity_core_<entry id>/`. Removing the integration deletes that folder.
- To stop it entirely, remove the integration in Home Assistant and press **Remove** on its row in Infinity Core ›
  Settings › Extensions.
