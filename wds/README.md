# WDS answer files (Windows Server 2019)

Eight files. Two client unattends (one per architecture, server-wide) and six
image unattends (one per OS per architecture, assigned to individual images).

```
client-unattend-x86.xml          server-wide, all x86 images
client-unattend-x64.xml          server-wide, all x64 images
client-unattend-arm64.xml        server-wide, all ARM64 images
client-unattend-arm.xml          symmetry only - see "32-bit ARM" below

image-unattend-win7-x86.xml      per image
image-unattend-win7-x64.xml
image-unattend-win81-x86.xml
image-unattend-win81-x64.xml
image-unattend-win10-x86.xml
image-unattend-win10-x64.xml
image-unattend-win10-arm64.xml
image-unattend-win11-x64.xml     not deployable by plain WDS - see below
image-unattend-win11-arm64.xml   not deployable by plain WDS - see below
```

## Why it is two kinds of file and not one

WDS never reads `autounattend.xml` from media. It splits the job in two, and
the two halves are assigned in completely different places:

| | Passes | Assigned | Scope |
|---|---|---|---|
| Client unattend | `windowsPE` | Server Properties → Client tab | One per **architecture**, server-wide |
| Image unattend | `specialize`, `oobeSystem` | Install image → Properties → General | One per **image** |

The client unattend cannot be per-OS — WDS only lets you nominate one file per
architecture for the whole server. That is why all the version-specific
settings live in the image unattends and the client unattends are near-empty.

## Assigning them

**Client unattend** — copy both files to `C:\RemoteInstall\WdsClientUnattend\`
on the server. Right-click the server → Properties → Client tab → tick
"Enable unattended installation" → Browse next to each architecture → OK.

The Client tab lists separate slots for legacy/BIOS and UEFI for each
architecture. **Assign the same file to both slots:**

| Slot | File |
|---|---|
| x86 (Legacy/BIOS) | `client-unattend-x86.xml` |
| x86 (UEFI) | `client-unattend-x86.xml` |
| x64 (Legacy/BIOS) | `client-unattend-x64.xml` |
| x64 (UEFI) | `client-unattend-x64.xml` |
| ARM64 (UEFI) | `client-unattend-arm64.xml` |
| ARM (UEFI), if listed | `client-unattend-arm.xml` |

ARM64 is UEFI-only — there is no legacy/BIOS ARM64, so expect a single slot.
Check whether your Server 2019 Client tab lists ARM64 at all; if it does not,
the file cannot be assigned server-wide and would have to be attached to a
prestaged computer object instead.

`processorArchitecture` refers to the CPU, not the firmware, and nothing in an
answer file's schema is BIOS- or UEFI-specific. The split exists so WDS knows
which file to hand a client based on how that client booted — it is a delivery
distinction, not a file-format one.

The reason WDS bothers to separate them is `<DiskConfiguration>`: BIOS/MBR and
UEFI/GPT need completely different partition layouts, so anyone automating
partitioning genuinely needs two different files. These files contain no
`<DiskConfiguration>` at all, so there is nothing firmware-dependent in them
and one file per architecture covers both slots. If you ever add automated
partitioning, that is the point at which you must split them.

The image unattends have no legacy/UEFI distinction — they are assigned per
image, and `specialize`/`oobeSystem` run long after firmware stopped mattering.

Two notes for older hardware:

- **Windows 7 32-bit cannot UEFI boot at all**; only x64 Windows 7 supports
  UEFI. The x86 (UEFI) slot is effectively dead weight for Windows 7 work.
  Fill it in anyway for consistency — it costs nothing.
- **Windows 7 x64 over UEFI needs Secure Boot disabled** in firmware. Windows 7
  predates Secure Boot and will not boot with it enabled.

**Image unattend** — Install Images → your image group → right-click an image →
Properties → General → tick "Allow image to install in unattended mode" →
Select File… → pick the matching file → OK. Match the OS *and* the
architecture; the `processorArchitecture` in the file has to match the image or
the components are silently ignored.

## Before you deploy: the account clash

Every image unattend creates a local account called `User` during OOBE. If the
golden image you captured already contains a `User` account — because you made
one while building it in the VM — the two collide.

Build golden images from the **built-in Administrator in audit mode**: boot the
VM, press `Ctrl+Shift+F3` at the OOBE screen, do your updates and tweaks, then
`sysprep /generalize /oobe /shutdown` and capture. Don't create `User` in the
VM; let the answer file create it at deploy time.

If an existing captured image already has `User` in it, delete the
`<UserAccounts>` block from that image's file and keep the rest — the password
fix still works, because `net accounts` is machine-wide.

## What these deliberately do not do

No `<DiskConfiguration>`, no `<ImageSelection>`, no `<InstallTo>`. Disk and
image selection still prompt, exactly like the USB answer files. Nothing here
can wipe a target disk unattended. Setting `ImageSelection` would force you to
set `InstallTo`, which forces a `DiskConfiguration` that repartitions without
asking — deliberately avoided.

No credentials in the client unattend either; you get prompted at the client.
A `<Credentials>` block is commented out in each client file if you want it —
read the next section first.

## Security: CVE-2026-0386 and the April 2026 hardening

**This may stop your unattended deployments working, and it is not something
these files can fix.**

Microsoft found that WDS transmits unattend answer files to clients over an
unauthenticated RPC channel, letting an attacker on the same network intercept
them — and, worse, achieve remote code execution. That is CVE-2026-0386. The
response came in two phases:

- **13 January 2026** — hands-free deployment still works, but new event-log
  alerts appear and a registry control is added to turn it off.
- **14 April 2026** — hands-free deployment is **disabled by default**. Servers
  with no explicit registry setting have the feature blocked after the April
  security update.

The override, if you need it:

```
HKLM\SYSTEM\CurrentControlSet\Services\WdsServer\Providers\WdsImgSrv\Unattend
    AllowHandsFreeFunctionality  (REG_DWORD)  = 1
```

Microsoft is explicit that setting this back to `1` is not a secure
configuration and is meant as a short-term bridge, not a permanent fix. The
practical mitigation is to keep the deployment VLAN isolated and off any
network you don't control, which for a bench setup is usually easy.

**Two things about this are unverified.** Whether the block covers the
*per-image* unattend as well as the client unattend, and whether Server 2019 is
affected at all (the coverage focuses on Windows 11 and Server 2025), could not
be confirmed — Microsoft's KB was unreachable at the time of writing. Check
your own server before assuming either way:

1. Look for that registry key. If it isn't there, your server probably hasn't
   received the hardening.
2. Check the WDS event log for the new hands-free alerts.
3. Deploy one machine. If it prompts for things the answer file should have
   filled in, the hardening is active.

Because of this, **do not put a real password in `<Value>`** in any of these
files unless you have confirmed your deployment network is isolated. A blank
password on a machine that is about to be handed to a customer is the safer
default anyway.

## ARM64

`image-unattend-win10-arm64.xml` and `image-unattend-win11-arm64.xml` are
ordinary image unattends and need no special handling. The awkward part is the
**boot image**, not the answer file.

A client unattend only runs inside a WinPE boot image of the *same*
architecture — WDS cannot serve an ARM64 client from an x64 boot image. So
`client-unattend-arm64.xml` does nothing until an ARM64 boot image exists on the
server, and getting one working is the real task:

- The ARM64 `boot.wim` must come from a recent Windows ADK, with the machine's
  NIC and storage drivers injected. x86/x64 drivers will not work — they must be
  ARM64 drivers, and many vendors still don't publish them.
- Without a working NIC driver, WinPE starts and immediately reboots. That looks
  like a PXE failure but isn't.
- Many ARM64 laptops have no Ethernet port and no usable network-boot entry in
  firmware. Snapdragon machines are widely reported to PXE-boot and then drop
  straight back to the UEFI menu, even through a USB or Thunderbolt Ethernet
  dongle.

Expect USB to be the route that actually works on an ARM64 laptop. The image
unattends are still the right files to use there.

## 32-bit ARM

`client-unattend-arm.xml` exists for symmetry and will almost certainly never
run. "arm" means 32-bit ARM (ARMv7) — Windows RT — and Microsoft never released
it as installation media. It shipped preinstalled only, on Surface RT, Surface 2
and a few OEM tablets, with a locked bootloader and Secure Boot that cannot be
disabled. There is no official ISO to add to WDS, and the hardware won't boot an
image you supply.

The only 32-bit ARM Windows in circulation is an unofficial community repack of
Windows 10 ARM32 for Surface RT hardware — a hobbyist project, not something WDS
can deploy. Windows 11 dropped 32-bit ARM entirely, so nothing new will need it.

There is no image unattend for 32-bit ARM, because there is no image to assign
one to.

## Windows 11 is here, but plain WDS cannot deploy it

Both `image-unattend-win11-x64.xml` and `image-unattend-win11-arm64.xml` exist,
because both images are on the server. They are correct files. What they are not
is deployable by the WDS console's own image deployment.

There is no 32-bit Windows 11, so that architecture is absent.
More importantly, starting with Windows 11 Microsoft blocks
`boot.wim` from installation media and blocks running Windows Setup in "WDS
mode" — the exact flow plain WDS uses to deploy an install image. PXE-booting
Windows 11 needs a custom WinPE boot image built with the Windows ADK, and
driving the install from something other than WDS's own image deployment.

Note that MDT is *not* the escape hatch: it isn't supported with Windows 11 or
with the Windows 11 ADK. Microsoft points at Configuration Manager, Autopilot,
or a hand-rolled WinPE workflow instead.

So these two files apply when you deploy those images by some route other than
plain WDS image deployment: a custom WinPE boot image built with the Windows
ADK, Configuration Manager, or applying the WIM by hand with DISM.

**For a Windows 11 USB stick, use `autounattend.xml` at the repository root
instead — not these files.** That is a different kind of file: all three passes
including `windowsPE`, both architectures in one file, and it goes at the root
of the stick. An image unattend has no `windowsPE` pass and only one
architecture, so it is not a substitute, and neither is the USB file a
substitute for it.

## Sources

- [WDS hands-free hardening / CVE-2026-0386](https://support.microsoft.com/en-us/topic/windows-deployment-services-wds-hands-free-deployment-hardening-guidance-related-to-cve-2026-0386-0daa3a3c-f3cd-4291-9147-a459c290c462)
- [WDS boot.wim support](https://learn.microsoft.com/en-us/windows/deployment/wds-boot-support)
- [Associate unattend files](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-R2-and-2008/cc732723(v=ws.10))
- [MDT known issues](https://learn.microsoft.com/en-us/intune/configmgr/mdt/known-issues)
