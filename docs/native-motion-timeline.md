# Client movement presentation

The client keeps the newest authoritative position in the native body for
input and game state. Drawing uses a separate timeline. An older geometry
snapshot updates structure and health without rewinding retained body poses;
re-applying a fast frame after loading geometry does not reconcile an existing
root a second time. Respawn and ownership changes start a fresh history;
strictly verified shape replacements can preserve the same actor's history.
Native campaign joins use a 100 ms presentation delay by default, configurable
from 0 to 200 ms. The legacy sandbox keeps zero delay. The DLL and older probe
fallback remain zero until campaign launch configuration selects the value.

Fast frames carry both the host's monotonic sample time and native simulation
time. The client estimates the
offset between monotonic clocks from the least delayed observed receipt, and
includes socket-to-native queue age. It projects each root from its own sample
time rather than restarting elapsed time when a scene finishes parsing. This
does not determine absolute one-way network delay; it removes variable delivery
and processing delay above the best observed path.

Native time uses direct `QueryPerformanceCounter` milliseconds. Processes on
one PC share that high-resolution epoch; clock-offset estimation handles
different PCs. Wire sample times retain integer milliseconds, while native
presentation retains fractional milliseconds. Coarse `GetTickCount64` values
had repeated positions and 15.625 ms jumps in recorded traces. Each presenter
pass evaluates all roots at one timestamp, and each motion batch accepts its
corrections at one timestamp. Camera and draw passes currently still have
separate timestamps.

Native velocities are measured per simulation second. A background or busy
host can draw smoothly while advancing fewer physics steps per wall second.
The presenter estimates simulation pace from four consecutive host samples,
then scales linear and angular extrapolation and projectile motion by that
pace. Projecting native velocity directly against wall time overestimated
travel in recorded tests where the host advanced only 0.43 simulation seconds
per wall second. Authoritative native body velocities remain in simulation
units.

With zero presentation delay, corrections preserve displayed
position and velocity at receipt. A 100 ms
Hermite curve removes the error without an instantaneous speed change. Angles
use the shortest equivalent turn. Extrapolation freezes after 250 ms without a
new sample. This is bounded extrapolation, not full input replay or buffered
snapshot interpolation. Experimental local pilot prediction remains gated.

`test/native_timeline_test.c` exercises the same C equations used by the DLL:
constant-velocity samples delivered at irregular intervals, reduced simulation
pace, recovery and pause, rotation crossing
the angle wrap, position and velocity continuity at correction, and stale
stream freeze. `test/native_timeline_test.py` compiles and runs that program with
the existing Zig compiler. These math tests do not establish visual parity
with vanilla gameplay; recordings and live input tests provide that evidence.

A configurable positive delay, up to 200 ms, enables twelve source-time
pose samples per root. The client evaluates `local now - delay - clock offset`
between immutable host-clock
samples using cubic Hermite positions and shortest wrapped angles, with
native simulation velocities converted to wall-time tangents once at sample
arrival. Later arrivals do not rewrite an already available bracket. Startup
holds the earliest sample; late delivery extrapolates at most 250 ms. Native
lifetime changes, teleports and gaps over 500 ms reset the buffer. Native
campaign launches select 100 ms following the recorded tests; the delay also
adds visible input latency while local prediction remains disabled.

Offset estimation can improve after startup when a less delayed frame arrives.
Buffered anchors and selected visual frames retain their original host sample
times, so an improved offset moves the shared evaluation time without changing
the intervals between stored samples. Legacy zero-delay correction timestamps
remain mapped to the client clock. A low-frequency four-value clock diagnostic
reports local time, estimated offset, newest motion source time and selected
visual source time. The C tests also cover unrelated host/client clock epochs
and invariant curve evaluation while the offset changes.

The JavaScript delivery layer selects the visual/health/mover frame at or
before that same delayed source time. Its source timestamp sets projectile
and beam age. Raw authoritative bodies retain the newest host pose. Applying
that pose restores the previously prepared render position, instead of
advancing display transforms on the simulation thread during a frame.

The stock camera must see the presented pilot position before it computes its
view. For executable SHA-256
`8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c`, recovered
assembly at RVA `0xf51e0` reads the controlled cluster's render point at
`+0xd8/+0xdc` (instructions `0xf52d8/0xf52e6`). `BlockCluster::setPos` at
`0x8c7b0` writes those render fields from the newly received authoritative pose.
`Body::setRenderPosAngle` at `0x5cae0` writes the previous, current and displayed
render positions and angles. Preparing presentation before native camera
follow preserves its existing pan, zoom and rotation behavior; preparing it
again before `GameZone::DrawGame` keeps block rendering on that timeline.

The recovered files in the sibling `Reassembly-Research` repository are
automatic pseudocode and disassembly, not the original game's source. Evidence:

- `recovered/win64/functions/unnamed/f51e0.asm`
- `recovered/win64/functions/BlockCluster/8c7b0.c`
- `recovered/win64/functions/Body/5cae0.asm`
- `recovered/win64/functions/BlockCluster/86c20.c`
- `recovered/win64/functions/GameZone/169130.c`

Native `advanceUpdate` refreshes its render history from the body;
`updateRenderPositions` interpolates that history before drawing. The
presentation helper therefore sets all three native render samples to the
same evaluated pose at the camera and draw boundaries, rather than changing
the stock view directly.

Simulation and drawing use different native threads. A private recursive
critical section protects the mod's timeline cache. Camera and draw callbacks
use a nonblocking attempt and skip an optional presentation update while a
mutation owns the cache. Particle callbacks copy immutable published curves
with bounded retries. Acquiring game zone or cluster locks from
these intercepted callbacks froze a recorded client test, so those locks are
not used. The cache lock does not protect native engine lifetimes; a verified
native mutation phase remains necessary before claiming complete concurrency
safety.

The host's AI interception uses a native prefilter in `native/host_ai.c`.
It crosses into the remote-control callback only for a configured faction and
pilot; other AI invokes the original native trampoline directly. A vacant
owned seat continues through the existing callback to native AI. The filter
uses the command and owning-cluster path read by `AI::update` at RVA `0x74150`,
checks the native entry before replacement and the returned trampoline's
executable mapping, publishes one immutable
ownership table, and reports native versus remote callback counts. The
standalone production-C routing test covers ownership, forwarding and vacant
fallback; live tests must also confirm native AI still runs and the host's
simulation pace improves.

The private cache guard covers timeline access, root pose mutation and the
matching render-pose restoration. Retained block health and resource writes
run on the owning update thread outside that guard; they do not change root
lifetime or timeline metadata. Structural removal remains guarded. Guard
statistics count skipped optional presenter/trace calls and corrected render
restorations, so recordings can distinguish frame cadence from contention.

A recorded early shape replacement exposed a new pilot body with sequence 0
and no history before fast replay initialized it. Replacement continuity now
requires an explicit planner ticket whose actor identity, owned COMMAND
block's persistent identity and canonical faction match both old and new
serialized roots. Native validation checks the real command block, its
owner, serial identity, faction and zone twice. The ticket keeps stable
identities only; it does not keep freed root, command or serial pointers.
Unannounced pointer changes or mismatched generations reset the history.
Valid rebinding restores the curve before first presentation and republishes
the emitter snapshot for the new pointer. Duplicate fast replay initializes
the new native body once without accepting the same curve sample or
reconciling prediction again. Production-shared replacement tests cover
command/faction changes, unreadable memory and generation resets. Rendering
between structural removal and append remains a separate transaction-phase
issue; continuity alone does not make that interval invisible.

The native campaign client's `sceneIdleGate` schedules source geometry and
fast-state mutations during the render-idle window. A source callback tries
an atomic IDLE-to-MUTATING transition and defers rather than waiting if the
renderer is active. It retains that transaction through native remove,
append, runtime update, newest-state restoration and player binding. The
completed frame remains available while mutation finishes. A failed commit
or a mutation exceeding the bounded 100 ms renderer wait terminates only
this configured private game process; it never forces the gate open over an
unfinished mutation. After the optimized three-minute combat regression
`1791108183557961700` passed with 10,732 client frames, p95 17 ms, maximum
23 ms and no frame interval above 50 ms, normal native campaign joins enable
the gate by default. The measured 6,138 transactions had maximum mutation
16.054 ms, maximum renderer wait 14.834 ms and zero timeouts. Hosts and
legacy sandbox clients retain their existing paths. Both `sceneIdleGate`
and the retained `testSceneIdleGate` alias require explicit boolean values;
either true enables the same verified native client path. Initial import,
executable signatures, distinct draw/update threads and strict ownership
checks still apply. Reports derive actual activation from the native
configured event or enabled state; requested fixture flags are separate.

The first gate closed IDLE at the end of the pre-Swap pacer. That recorded
fixture had source acceptance gaps up to 402 ms despite preparation gaps no
larger than 126 ms. Source callbacks can naturally follow Swap, so closing
the window before that phase can repeatedly defer them until scheduling
drifts. The next experiment leaves IDLE open through Swap and deferred
processing, then closes or waits at the entry to native Render, RVA
`0x118730`, before invoking its original trampoline. Its fifteen-byte entry
prefix consists of complete instructions; RCX carries the GameState pointer.
The wait executes natively outside the JavaScript isolate lock.

The exact caller at `0x169d70` releases the GC lock `0x3cf178` and queued-task
lock `0x3cff30` before calling Render. Render's own GameState SRW acquisition
begins at `0x11879d`; the replacement waits before that acquisition. Swap
and `Globals::processDeferredFromMainThread` at `0x15ea10` follow the finished
draw. Deferred processing and the non-main enqueue helper `0x91a00` both
use Globals `+0xf8`. `pool_free_mainthread` at `0x7e920` marks the removed
object and enqueues its deletion through that helper. Deferred processing
holds the queue mutex while invoking queued callbacks, and releases it
before returning to the next Render entry. Source enqueueing may therefore
wait for that native queue mutex, but the new Render-entry wait does not
retain it. No new engine locks or lifetime pins are introduced by the gate.

The root-count readback no longer holds the presentation cache across a
native identity scan: it does not access mod timeline data, and the private
field's configured zone/update-thread validation still applies. A read-only
scan had coincided with four recorded frames where both presentation stages
skipped; stock `advanceUpdate` and `updateRenderPositions` can then expose
raw render history. Removing unrelated reader contention is preferred to
writing native render fields concurrently through an unpinned fallback.
The two-minute quiet fixture `1791102558727210300` exercised the Render-entry
gate, event wake-up and unguarded root-count readback together. It had 7,134
client display frames, p95 17 ms, maximum 23 ms and no interval above 50 ms.
All presentation/trace busy counters remained zero. Each of the 3,148
retained pilot trace samples had an initialized sequence and exact equality
between intended and actual native render positions. Maximum mutation time
was 17.444 ms, maximum renderer wait 16.552 ms, and gate timeouts were zero.
These combined results do not isolate which change removed contention;
combat verification remains required before normal gate activation.

Local exhaust transformed 255,050 emissions with no unavailable snapshot,
publication miss or invalid-root counts, but skipped 54 stale curves. Those
skips occurred between world 212/frame 3,192 and world 213/frame 3,204,
around fixture 52.65–52.84 seconds, rather than during startup. The pilot
remained on source sequence 970 through 52.684 seconds (223.64 ms age), then
advanced to 977 at 52.856 seconds. The source exported sequences 971–977
at ordinary 50–68 ms intervals. Overall source and client receive gaps
reached 85 and 94 ms, while native acceptance gaps reached 368 ms. This
locates a residual client application delay; the low-frequency exhaust
counter does not identify which roots contributed all 54 skips. The
fixture did not enable per-nozzle thrust audit, so it does not establish a
new plume-size or emission-velocity comparison.

## Recorded verification

The two-minute local quiet-flight run
`1791092794981390600` used a 100 ms presentation delay, immutable source-clock
anchors and disabled local pilot prediction. The native harness reported no
failures. It sampled 7,125 client frame intervals: the 95th percentile was
17 ms, the maximum was 21 ms, and none exceeded 50 ms. The host's recorded
maximum interval was 19 ms, also with no interval over 50 ms. These are native
swap intervals; video analysis separately checks the rendered movement. The
paired 59.967-second videos captured approximately 41.1 client and 40.2 host
frames per second. Capture intervals reached 50 ms, with none over 50 ms, so
these videos do not contain every native 60 Hz frame.

Pixel review checked independent scenery as well as the pilot, since the
native camera can hold a moving pilot at a fixed screen position. In the
43.33–46.00-second client clip window, a separately tracked large asteroid
moved 268.09 pixels across 107 captured frames; its 200 ms chord residual was
1.823 pixels at the 95th percentile. Four isolated 16.7 ms near-static capture
intervals did not form a sustained freeze. Exact association with native cache
busy events is unavailable because these videos lack the first-capture wall
timestamp and the native traces sample at approximately 30 Hz. This review
does not establish camera parity for every displayed frame.

The moving pilot had 2,904 valid post-draw trace samples and no measured moving
stall runs. Its actual native render point matched the intended interpolated
point at the 95th percentile; one sample differed by more than 0.01 world units,
with a maximum difference of 0.115 units. Across all presented roots, 97.4% of
evaluations had an interpolation bracket and 1.24% used bounded extrapolation.
The remaining evaluations were startup holds or stale freezes.

Camera and drawing paired on the same display frame and thread in all 2,904
samples. Their start times differed by 0.059 ms at the median and 0.141 ms at
the 95th percentile, with a 3.636 ms maximum. Those measurements do not justify
adding another camera hook solely to share a timestamp. Separate passes remain
explicit in the diagnostic trace.

The trace analyser estimates motion using 200 ms central differences. For the
owned pilot, the corresponding host and client results were:

| Flight phase | Host median speed | Client median speed | Host acceleration, p95 | Client acceleration, p95 |
| --- | ---: | ---: | ---: | ---: |
| Steady forward | 185.02 | 184.62 | 71.17 | 139.02 |
| Turning | 183.81 | 183.69 | 136.49 | 153.60 |
| Braking | 54.33 | 54.97 | 110.92 | 121.55 |

Speed is in world units per wall second; acceleration is in world units per
wall second squared. Travel speed now closely matches the host, while the
forward acceleration trace still shows extra small speed variations. The
stored Hermite tangents use a four-sample simulation-rate estimate; individual
brackets can have different rates because native simulation steps are discrete.
Tangent policy remains unchanged until the recordings establish whether that
residual difference is visible and warrants another change.

The shared late fixture window, 60–118.4 seconds, was closer: acceleration
at the 95th percentile was 73.8 client versus 64.6 host during forward flight,
180.1 versus 154.3 while turning, and 116.7 versus 107.5 while braking. The
earlier matched forward segment around fixture seconds 42–47.7 remained
239.2 client versus 74.6 host, so the improvement does not establish uniform
vanilla motion across every segment. The trace buffers are
bounded, retaining client data from fixture time 15.486 seconds and host data
from approximately 28.2 seconds; neither covers the entire startup.

A 100 ms source delay does not imply an exact 100 ms difference between the
two displayed game windows. The stock host renderer itself interpolates behind
its newest native body pose: the measured raw-to-render delay was 10.8 ms at
the median and 16.7 ms at the 95th percentile during movement. In this run,
the best constant fit between host and client rendered positions was 80 ms,
with a 1.04-unit position error at the 95th percentile. That fitted lag describes
the recording and does not replace the configured source timeline.

The delay smooths network arrivals but adds approximately 100 ms to visible
control response while full input replay remains disabled. Campaign launch
configuration selects 100 ms; the underlying native fallback stays zero. These quiet
tests do not establish wide-area network latency, combat or ship-editor parity.
The source instrumentation and harness result are under
`.runtime/live-{host,client}-1791092794981390600`; compact JSON comparisons and
review images are under `.runtime/smoothness-recordings/1791092794981390600`.
Old MP4s are rotated as newer recorded comparisons replace them.

The subsequent 180-second combat probe `1791094093884171700` passed its native
remote-control postconditions, including weapon use, one remote respawn,
vacant-seat AI and opening/closing both native menu tabs on each side. Health
readback compared 37,516 common block states across 684 snapshots with zero
maximum error. Of 10,728 client frame intervals, the 95th percentile was 19 ms;
one interval exceeded 50 ms and reached 113 ms. That isolated hitch remains a
performance limit even though the test reported no native errors. This probe
does not validate authoritative remote editing or all multiplayer gameplay.

The frame pacer retains its existing 60 Hz deadline and resets when it falls
more than four steps behind. Its kernel wait now requests the remaining
deadline time rounded up plus 2 ms, capped at 25 ms. The previous fixed 100 ms
timeout could intentionally wait much longer if a timer signal were lost;
that was a plausible risk, not a proven cause of the recorded 113 ms interval.
Windows scheduling can still delay a thread beyond its requested timeout.
`RepopulatedNativePaceStats` reports lifetime calls, waits, timeouts, failures,
maximum actual/requested wait, deadline resets and the last Win32 wait result
at the existing low-frequency telemetry cadence. The production-shared C test
checks cadence and catchup, failure/timeout paths and a real high-resolution
kernel timer without launching the game.

## Health and native lifetime audit

Realtime health packets retain stable block identities and numeric values,
not native pointers. `RepopulatedApplyRealtimeHealth` walks the current zone's
live roots and block vectors on the owning update thread, finds each identity
in the sorted packet, then writes health, growth and lifetime scalars. Retained
geometry follows the same identity lookup. Health audit telemetry compares
identities common to the selected visual frame and current pilot, since block
membership changes travel on the slower geometry channel.

Those writes do not directly remove a native object. Structural removal uses
the game's remove-from-zone, recursive kill and main-thread pool-free helpers.
For the exact binary above, the recovered `pool_free_mainthread` at RVA
`0x7e920` invokes `onQueueForDelete`, frees immediately on the game's recorded
main thread, and otherwise queues the release. The recovered
`removeFromGameZone` at `0x8cf40` erases zone membership before
`killRecursive` at `0x7f150`. This is static evidence about the native path,
not a proof that every render/update interleaving is safe.

`ReadProcessMemory` validates an access at that instant; it does not pin an
object until a subsequent native call or scalar write. The private timeline
guard also cannot stop the game's own deferred deletion queue. Damage,
destruction, respawn and interest-boundary combat recordings therefore remain
necessary before claiming complete native lifetime safety. Test-only motion
trace pointers verify current identity, zone ownership and freshness before
publication, and publish a zero row when that validation fails.

## Test-only matched spectator view

A fair ship/exhaust comparison needs both recordings to show the same native
ship at the same scale. The test configuration
`testComparisonView={ident:0x70000002,zoom:2}` selects the remote pilot in both
processes. It reads that ship's actual render position through the existing
validated two-ship trace helper after client presentation or the host's native
render-position update. Returned positions are translated back into each
process's local native coordinates using the trace's visual center.

The test copies the 72-byte `View` passed to `GameZone::DrawGame` and changes
only fields in that temporary copy:

| View offset | Test value |
| --- | --- |
| `+0x10`, `+0x14` | Target ship's actual native render position |
| `+0x18`, `+0x1c` | Zero camera movement delta |
| `+0x20` | Fixed zoom of 2 |
| `+0x28`, `+0x2c` | Fixed orientation vector `(1,0)` |

Viewport, clipping and other fields are preserved. The copied argument stays
alive through the original world draw and its projectile/beam passes; native
Player, physics and input retain the original camera view. This configuration
belongs only to the recorded test harness and does not change launcher or
gameplay flows. On a busy trace cache, `RepopulatedReadComparisonFocus` makes
a bounded read of current native zone roots and the target's actual render
pair. It validates the requested identity, zone owner and root membership
before and after the pose read, rejects ambiguous duplicate identities and
checks finite coordinate bounds. This helper takes no engine/cache locks and
does not pin native lifetimes. A successful read returns coordinates already
including the local sector offset. Missing or unreadable targets use the
original View with explicit `fallback-native` telemetry, avoiding the stale
held focus that introduced a synthetic camera offset in earlier recordings.

Static ABI evidence for the exact executable is the nine consecutive
eight-byte `View` copies in recovered `GSFly::vfunction4` at RVA `0x11c3e0`,
the same 72-byte local copy in `GameZone::DrawGame` at `0x167580`, and the
center/zoom/orientation reads in `View::getWorldShaderState` at `0x28b80`.
The native camera at `0xf51e0` writes the movement delta at `+0x18/+0x1c`.
Matched video and logged view fields still need to verify the test's actual
rendered framing; static evidence alone does not establish plume parity.

## Local exhaust clock audit

The matched-view recording `1791095395287862800` reveals an effect difference:
the native host's thin connected exhaust and the client's brighter spaced
particles differ at the same pilot and zoom. Both processes compile the same
points variant of `ShaderParticlePoints`, and their measured simulation pace
is approximately one SIM second per wall second. Host rendering was 240 FPS
and client rendering 60 FPS. The subsequent matched-60-Hz recording
`1791097113862911100` still shows that plume difference around 5 and 12
seconds. Render cadence alone therefore does not explain the appearance.
That run stopped on a strict faction mismatch and is visual diagnostic
evidence, rather than a passed stability test.

For the exact executable above, `ParticleSystem::update` at RVA `0x45310`
receives the zone's simulation step and time at call `0x164f15`. Its render
helper at `0x458c0` receives the interpolated zone time `+0x15c` from call
`0x168141`; `GameZone::updateRenderPositions` at `0x169130` computes that time
between `simTime-dt` and `simTime`. The shipped particle shader uses fixed
point size from `ToPixels` and the particle's stored size, with age-dependent
alpha and velocity. It has no explicit render-FPS size factor.

Native `Block::moverUpdate` at `0xf09e0` constructs thrust sizes from the
smoothed throttle, response multiplier and thruster dimensions. Simulation dt
affects response smoothing. The thrust emitter at `0x1cc440` produces two
particles per accepted call; dt sets a minimum particle lifetime. The local
client wrapper preserves native size/color arguments. An earlier version
attached emissions to the last prepared display pose; the update thread can
emit several times before that pose advances.

The private fixture flag `testThrustAudit=0x70000002` now measures native
emissions from that exact pilot and stable nozzle block. The host's native
mover wrapper forwards directly to the original helper, while the client's
existing local mover call scopes the same diagnostic context. Context is
thread-local and lives only around that native call. The 512-row FIFO stores
QPC time, particle-system time, simulation dt, raw/render poses, the actual
two emission velocity/size/color arguments, throttle and mover dimensions.
The client records arguments after its origin transform; the host
records unchanged original arguments. Reads drain at most 128 rows per
sample, and contention or overflow drops diagnostics rather than waiting
inside a native callback. No lock spans the original mover or thrust call.

Host diagnostic-only capture does not fill the old network particle queue.
Normal gameplay receives no new particle payload, hooks or per-frame logs.
These reads observe memory at that instant and do not pin engine objects or
prove every emission and render interleaving is coherent. The shared C test
checks immutable FIFO ordering, wraparound, overflow, nonblocking contention
and context isolation between native threads.

The paired 65-second audit (`1791098592982590600`) contained 84,745 host and
87,615 client emission records with only four and one diagnostic drops.
Both native simulations used dt 1/60, matching stable nozzle dimensions and
power. At full throttle the main nozzle's median size was 6.3245847 on both
sides; median emission intervals were 16.79 ms and 16.75 ms. A further
particle-render audit (`1791099656591091600`) measured the same stable
`ToPixels` value 0.75, zero View margin, point rendering mode and allocation
of 32,768 particles against a 524,288 limit. These measurements do not justify
a particle-size multiplier.

The steady-flight origin measurements identify a placement discrepancy.
Selecting consecutive emissions with speed above 170, stable angle/velocity
and interval below 40 ms gave 2,165 host and 1,974 client pairs. Host origins
never repeated and advanced a median 3.099 world units per emission. The
client repeated an origin in 139 pairs (7.0%), then sometimes advanced much
farther: displacement p95 was 5.945 versus 3.130 host units. Repeated native
particle emission from a stale display point creates overlapping bright dots
and gaps even with correct native sizes and draw rates.

The client now evaluates each cosmetic emission at its own high-resolution
QPC timestamp on the same source-clock curve and 100 ms delay used for ship
presentation. Three immutable publication banks provide bounded copies
without taking the presentation cache or game locks. The key must match
both root identity and the exact live root pointer. Removal invalidates the
key; unavailable metadata or a curve beyond its 250 ms horizon skips that
cosmetic emission. The latest native raw body remains authoritative input
to the game. Only particle origin and velocity arguments are transformed:
the buffered curve supplies ship velocity and angular velocity, converted
from wall-second to source-simulation-second units, while each nozzle's
relative exhaust velocity, native size, color and lifetime remain intact.

Production-shared tests verify source-clock progress during 100 ms without
display preparations, unrelated clock epochs, shortest wrapped rotation,
simulation velocity conversion, stale/missing/root-mismatch rejection and
nonblocking bank exhaustion. A 50,000-publication test with three concurrent
readers checks that every successful snapshot is internally consistent.
These tests do not establish live plume parity; a new paired native
recording must measure the resulting origin spacing and visible trail.
`RepopulatedLocalExhaustStats` reports lifetime transformed, unavailable,
stale, publication-miss and invalid-root counts at low frequency.

The combined 65-second recorded fixture (`1791101188259397000`; video folder
`1791101188261990600`) exercised the published curve with zero missing,
publication-miss or invalid-root counts. Under the previous steady-flight
filters, none of the 27 matching nozzles repeated an emission origin. The
main nozzle had 2,534 host and 2,144 client qualifying pairs: median origin
displacements were 3.098 and 3.088 units, p95 3.129 and 3.565 units, and
maximum 3.397 and 5.411 units. The source-generated persistent nozzle ID
changed between fixtures, so the new main nozzle is `0x78000ea9`; both sides
still share its native size, dimensions and power. This improves on the
prior client's 139 repeated origins and 20.664-unit maximum displacement.
The raw client body repeated in 1,830 of these pairs, demonstrating that the
cosmetic curve no longer depends on raw packet cadence.

There were 708 stale-curve skips near fixture 1.7, 34.7–34.9 and 45.7–46.5
seconds; motion acceptance interval reached 402 ms while preparation interval
reached 126 ms. The main nozzle's startup gap reached 383 ms. This is a
remaining source-delivery/acceptance observation, not publication contention.
The native client had 3,832 frames, p95 19 ms, maximum 40 ms and no interval
above 50 ms. Paired images still show a different plume appearance in some
turning phases. The position-spacing improvement and stable native size
measurements do not establish complete cosmetic parity. Buffered Hermite
velocity derivatives can also differ from source simulation velocity;
the matched main-nozzle particle velocity error at 100 ms lag was median
3.581 and p95 19.338 native units over 2,040 steady pairs.

A controlled cosmetic experiment now retains authoritative simulation
velocities in each immutable source sample beside that sample's captured
wall/simulation rate. Hermite rendering converts those endpoint velocities
with their captured rates, producing the same position, wrapped angle and
derivatives as the previous stored wall-tangent representation. Particle
birth position and angle still use that displayed curve, while their ship
velocity/angular baseline uses linear interpolation of the original native
simulation endpoints. The nozzle transform no longer divides that baseline
by the latest rolling rate. Its native relative exhaust speed, size, color,
lifetime and throttle are preserved.

The shared emitter test compares all six displayed outputs exactly at 671
timestamps across irregular samples, changing rates, a paused rate and
wrapped turns. It separately checks authoritative velocity interpolation,
zero-delay and zero-rate production buffers, the 250 ms stale horizon and
concurrent publication. Production timeline acceptance always creates a
source buffer, and same-generation replacement retains it. An unbuffered
legacy rate-one input can use its already-native velocity directly; missing
non-unit-rate metadata skips the cosmetic emission rather than reconstructing
a velocity. The emitter, timeline and replacement C runners pass. This is a
semantic correction under recorded evaluation, not evidence yet of matching
plume appearance.

The subsequent two-minute recorded experiment `1791104182753615400`
(`1791104182754121500` video folder) passed with 7,136 client display frames,
p95 17 ms, maximum 24 ms, no interval above 50 ms and zero presentation-busy
or exhaust-skip counters. The 262,167 transformed emissions had no missing
snapshot, publication miss, stale curve or invalid-root count. The pilot
kept the same command generation; early shape changes retained that command.

The bounded audit retained 179,194 host and 180,730 client emission rows,
with eight and six diagnostic drops, across 27 matching persistent nozzles.
For the main nozzle `0x78001795`, the prior steady-pair filters found 5,179
host and 4,333 client pairs with zero repeated origins. Origin displacement
was median 3.098 versus 3.034 units, p95 3.127 versus 3.326, and maximum
3.219 versus 4.796. The inferred trace clock offset was 2.125 ms, giving a
102.125 ms source alignment. Main particle-velocity error at that alignment
was median 0.740 and p95 2.324 native units across 5,178 active samples.
Turning samples separately had median 0.784 and p95 2.347 units across
2,588 samples. Those values include nozzle-relative and rotational velocity,
rather than measuring the linear ship baseline alone.

The images still show dotted client turn trails versus finer host trails.
Main turning throttle absolute error was median 0.119 and p95 0.533; native
first-particle size error was median 0.711 and p95 2.700. Particle simulation
pace was 0.954 host versus 0.999 client SIM seconds per wall second. These
remaining emission-argument differences require a separate throttle/response
audit; they do not justify an arbitrary size multiplier. The compact
`client-analysis-thrust-native-sim.json` retains all nozzle comparisons and
their limits. Different trajectories between fixtures prevent treating the
prior 65-second audit as a controlled statistical repeat.

The next bounded cosmetic experiment gives native movers two accepted
source control frames on the same delayed source timeline. `mover_window.h`
stores scalar rows only, keyed by actor and persistent nozzle identity;
matching acceleration/angular inputs interpolate between those endpoints.
An added nozzle has no authority before its future boundary, and a removed
or changed-owner nozzle cannot inherit another actor's input. With no future
frame, the old endpoint holds for at most 250 ms. An actual empty future
frame clears authority at its boundary. Explicit zero inputs still run the
native response decay. Angular controls are bounded scalar inputs rather
than wrapped rotation angles.

`RepopulatedApplyMoverWindow` validates both sorted 16-byte arrays, finite
timestamps, ordering and a maximum 500 ms source gap before mutating the
active window. The legacy untimed API clears that window. Timed apply and
native mover reads require the existing private Console's immutable owner
update thread: the GameZone update entry/exit callback writes the window,
and the directly replaced Block update reads it on that same thread. This
adds no object pointers, engine locks or presentation-cache locks. Native
`moverUpdate` still calculates sizes, colors and smoothing; the existing
force, torque and energy restoration remains in place. Shared C tests cover
irregular update times, identity/lifetime boundaries, empty endpoints,
unchanged state on rejection, wire tolerances and stale limits. This remains
a recorded experiment until emission arguments and visible effects verify
its behavior.

The 65-second mover-window fixture `1791105414915102000` passed with 3,835
client display frames, a maximum 50 ms interval, no interval above 50 ms,
and zero gate timeouts or exhaust-skip counters. It transformed 159,135
emissions and executed 415,886 local mover updates on the validated owner
thread. The audit retained 87,894 host and 90,537 client rows across the same
27 nozzle roles, with one host diagnostic drop and none on the client.

Comparing the same 100,000-power, 316.228-dimension nozzle role with the prior
SIM-velocity fixture, turning throttle absolute error changed from median
0.119/p95 0.533 to approximately zero/0.359. Smoothed native response error
changed from 0.051/0.133 to 0.00194/0.0832, and first-particle size error from
0.711/2.700 to 0.0135/1.822. The new role uses persistent block identity
`0x78001a26`; the highest-count steady nozzle is a different, smaller role
and cannot support that direct size comparison. Trajectories, source
simulation pace and fixture lengths differ, so these are descriptive
comparisons rather than a controlled repeat. Complete plume appearance
parity is still not established.

The maximum gated transaction was 43.08 ms. Geometry apply reached 45 ms;
its remove, append and runtime stages reached 21, 16 and 19 ms respectively.
The mover-window apply counter covered 1,219 calls, with mean 0.00246 ms,
p95 zero at millisecond resolution and maximum 1 ms. The geometry load is
therefore the evidenced long transaction, rather than the bounded scalar
window copy. Per-update endpoint lookup uses two bounded binary searches;
the counters do not separately measure that lookup. All nozzle comparisons
and the matching-role caveat are saved in
`client-analysis-thrust-mover-window.json`.

## Serialized ownership and commandless debris

The matched-60-Hz run above reproduced a previously intermittent fatal guard:
motion sequence 416, row 11, identity `0x7000005d`, source faction 8 versus
client faction 0. The source row had zero velocity, angular velocity and
command resources. Its exact serialized command state was not preserved,
so the failing root cannot be classified retrospectively with certainty.

The native format audit found a concrete inconsistency: the source fast
stream read the cluster's mutable faction cache at `+0x118`, while geometry
uses an owned serialized command's faction or neutral faction 0 for a
commandless root. In the exact executable, `updateFeatures` at RVA `0x84bf0`
updates that cache from a command's serialized faction or a block's cached
affiliation at `+0x54` (write at `0x8512a`). A commandless fragment can retain
an affiliation that geometry does not serialize.

Fast motion and the client root index now share the serialized ownership
definition: require the exact owned command identity and faction when one
exists; otherwise use neutral ownership and the minimum stable block
identity. This changes neither the source game's cached faction nor its AI
allegiance. True command-owner changes and duplicate identities still fail
strictly. The error now reports the canonical faction, native cached faction
and owned-command pointer for diagnosis. `RepopulatedOwnershipStats` counts
source/client commandless non-neutral caches and owned-command/cache
differences; its lifetime counts refer to inspected rows, not unique roots.

Production-shared decoding tests cover neutral fragments, owned faction
changes, mismatched/unreadable identities and rejection of negative factions.
Planner tests separately verify that a serialized command faction change
requires structural replacement. A recorded 65-second regression and a
subsequent 180-second combat regression passed without the faction fault.
The combat source counted 27,363 inspected commandless rows with non-neutral
native caches, exercising the canonical branch. The combat check also
passed health, respawn, save/resume, native menus, vacancy and backpressure
postconditions, with 10,725 client frames, p95 17 ms and maximum 24 ms.
This does not prove the unpreserved failing root's exact original state.
An owned-command faction transition that precedes its geometry still retains
the strict guard until there is evidence for a different lifecycle policy.

## Transient grid background during scene append

The failed matched-60-Hz video also contains isolated client grid frames at
17.916, 18.116 and 18.333 seconds, while the pilot remains visible. Geometry
append temporarily cleared the real zone's streamer at `+0x248` to obtain a
private Console field. Concurrent `GLScope::set` at RVA `0x1f7370` selects the
grid background when that streamer is absent. This explains the observed
scene flashes independently of pose interpolation.

Recovered `Console::getField` at `0xcab60` chooses the streamer's virtual
getter at vtable `+0x30`, or the private field cached at Console `+0x2f8`.
`GameZone::insert` at `0x133290` does not read the streamer. The replacement
therefore confines field isolation to a configured private Console, using a
persistent shadow Console and minimal shadow zone for the original getter.
The actual zone's streamer pointer is never changed. Other Consoles forward
to the original getter, and an immutable owner-thread check prevents using
the private field from another update thread.

The bounded C tests verify existing private-field pointer preservation and
that the real Console/zone are not written. The replacement does not add
engine locks. The paired 65-second recording found zero grid-signature
candidates in 2,439 client frames and 2,380 host frames, compared with seven
frames in six runs of the earlier client recording. The private field
forwarded 460 calls with zero owner-thread rejections. This verifies that
observed flash signature for the recorded fixture; structural remove/append
visibility and rare busy-presentation interleavings remain separate
lifecycle concerns.

## Batched geometry removal

The crowded mover-window fixture spent 19 ms removing 36 commandless roots
inside a 43.08 ms geometry transaction. Each prior removal crossed the
instrumentation boundary and scanned native identities again. The native
batch API now stages and validates all sorted removal descriptors before
mutation, resolves the top-root identity index and native removal functions
once, and retains only candidate addresses for the duration of that call.
It requires the configured private Console's current zone/update thread and
the scene gate's active mutation transaction; legacy per-root APIs remain.

Candidates are not lifetime pins. Before each removal, the batch copies the
current live zone vector, verifies exactly one pointer membership, and
rechecks zone ownership, null parent and exact stable root identity twice.
A missing candidate clears its old history/publication/trace without reading
through that address. A present reused or ambiguous candidate fails with
row, identity, pointer and already-removed count. Replacement continuity
still requires the owned command's persistent block ID and canonical faction;
the emitter publication and pilot/trace pointers are invalidated before
native kill or release.

This policy follows the exact executable SHA-256 recorded above.
`GameZone::erase` at RVA `0x1334a0` swap-erases the root vector, so cached
indices are unsuitable; the batch still calls native remove with index -1.
`removeFromGameZone` at `0x8cf40` and `killRecursive` at `0x7f150` recurse
through this root's attached child vector, while `kill` at `0x7f320`
destroys its blocks. Detached `doDeathExplode` at `0x8b070` skips the zone
damage path, and the inspected assembler/tractor/sound cleanup contains no
independent top-root deletion path. A pooled object can remain readable at
`pool_free` (`0x7e7d0`), making fresh membership/ownership/identity necessary
despite that static callback audit. `pool_free_mainthread` (`0x7e920`)
defers off-main-thread release. These are exact-build static observations,
not an object lifetime guarantee for unverified native code or a bad child
graph.

The production-shared C policy test covers invalid input before mutation,
private transaction authority, swap/reordered membership, recursive
disappearance, readable address reuse with a different identity, changed
parent/owner, missing and newly unindexed roots, bounded read failure,
same-command replacement and the 4,096-descriptor limit. The tests passed.
The subsequent optimized 180-second combat fixture exercised live batch
removal and same-command/lifecycle validation; its timing is recorded below.

## Geometry and source block-read batching

The next 180-second combat fixture passed gameplay postconditions but failed
its smoothness criterion: 15 native client frame intervals exceeded 50 ms,
maximum 98 ms, with a maximum geometry transaction of 77.996 ms. Bulk removal
reduced its measured p95/maximum stage to 3/12 ms; append remained 16/28 ms
and runtime updates 18/35 ms. These late crowded failures were beyond the
first-minute recording, motivating a later capture window for the next run.

Native loader messages identify a separate append floor. Across 260 retained
applications, median/p95/maximum native load-handler time was 7.3/13.7/26.7 ms;
parsing accounted for 5.7/10.7/21.5 ms and expansion 1.4/2.6/4.9 ms. File
reading remained at most 0.7 ms, and injection rounded to 0.0 ms. The final
1.4 MB additions reported 19.9 ms parse plus 4.9 ms expansion. Caching wrapper
function lookups alone cannot bring those applications below a 16 ms budget.
Detached parsing outside the visibility transaction has not been implemented.

The shared `replica_runtime_health.h` now copies each fresh block-pointer
vector once and decodes one contiguous native block header containing its
persistent ID, feature bits, lifetime, growth, health and owner. Private
runtime writes use this path only after verifying the configured Console's
zone/update thread and active mutation transaction. The initial import and
legacy runtime paths retain their prior behavior. The root index's
commandless minimum-ID scan and the child-root health pass use bounded fresh
vectors without retaining native pointers across callbacks. Wrong owner,
unreadable/malformed vectors and missing required top-root block IDs still
fail; absent optional child IDs still skip.

Source `ReadRealtime` likewise copies fresh root/block vectors and validates
native owners before reading the shared header. Its existing ancestry depth,
focus filter, stable-ID rules, health clamp and weapon/mover output fields
remain. Mover lookup now runs only for the feature families that were already
eligible for the outgoing mover row, avoiding an index read for every armor
block. This changes no native mover input, simulation step or wire layout.

The shared C test verifies complete block-byte equivalence after writes,
native maximum-health lookup, -1 growth/lifetime sentinels, explicit zero,
source health/feature preservation, optional child behavior and health-count
telemetry, owner/bounds/read failures and the 4,096-block limit. A minimum-ID
scan of 4,096 blocks now makes 4,098 guarded reads, versus the prior 8,193;
source armor health extraction combines six per-block reads into one header
plus its vector copy. Tests passed. The native parsing cost remains.

The optimized 180-second combat fixture
(`coop-check-1791108183557961700.json`) passed all functional postconditions
and its native smoothness criterion. It recorded 10,732 client display frames,
p95/maximum intervals of 17/23 ms and zero intervals over 50 ms. The host
recorded 43,573 sampled intervals, p95/maximum 5/20 ms and zero over 50 ms;
the normal host is not capped to the client's 60 Hz display cadence. These
measure native frame intervals, not the compositor's captured frame rate.

The scene gate completed 6,138 transactions with zero timeouts. Maximum
transaction/render-wait durations were 16.054/14.834 ms. Measured geometry
stage p95/maximum durations were removal 2/3 ms, append 9/13 ms, runtime
update 2/3 ms and accepted-pose application 1/2 ms. Full geometry application
p95/maximum was 15/27 ms, including work outside the visibility transaction.
Motion preparation p95/maximum fell to 1/2 ms; source realtime reading was
4/5 ms and the combined source motion export 4/6 ms. Health validation
compared 31,136 blocks over 556 snapshots with zero error. One real remote
respawn, native menus, firing, control backpressure and vacant-faction AI were
exercised. Presenter/trace cache-busy counts and stale exhaust skips were zero.

The largest source sample gap, 328 ms between motion sequences 173 and 174,
contains the explicit fixture destruction and successful remote respawn in
the host event order. The client's corresponding observed acceptance gap was
335 ms. A second 318 ms source gap (182 to 183) crosses the host's ship-menu
close transition; its client observation gap was 316 ms. Excluding those
lifecycle/menu intervals, the largest source sample gap was 90 ms. Complete
client presentation traces observed sequence-change gaps up to 153 ms;
their roughly 33 ms sampling adds uncertainty to the exact acceptance time.
Retained `native-stage` events are independently bounded and cannot be used
as a complete acceptance history. The normal source is approximately 20 Hz,
and the display remains delayed by 100 ms rather than claiming zero latency.

Local exhaust transformed 881,195 emissions. Twelve emissions lacked a
published source curve and were skipped cosmetically; stale-curve,
publication-miss and invalid-root counters were zero. Those twelve occurred
before the first retained world report, so their exact actor/time is not
recoverable from these aggregate counters. New geometry can legitimately
precede its first motion sample, but this report does not attribute each
missing snapshot to a particular debris lifetime.

This is an observed improvement on a later randomized combat world, not a
fully controlled repeat of identical geometry or damage. The earlier
98 ms failure and later 23 ms maximum establish the tested outcomes; they
do not isolate the contribution of each read/codec/packing change. Late video
capture covers the previous failure interval. Detached parsing/expansion
remains unimplemented: nested command blueprints can expand native clusters
during parsing, and exact-build parser/shape routines touch shared state.

## Final moving fixture with the production gate

The final 120-second moving fixture
(`live-controls-result-1791108942077607000.json`) used the enabled campaign
scene gate, the 100 ms source-time presentation buffer and the test-only
matched spectator view. Both games were capped to 60 Hz for this comparison;
the spectator view and host cap are diagnostic settings, not normal player
flows. Native client intervals over 7,012 samples had p95/maximum 17/23 ms,
with zero intervals over 50 ms. The host's 7,285 sampled intervals had
p95/maximum 17/18 ms and zero over 50 ms. The gate completed 4,084 transactions
with zero timeouts and maximum transaction/render-wait durations of
18.923/17.748 ms.

All 3,196 sampled pilot records and 2,116 valid peer records had exact agreement
between intended presentation and actual native render position/angle. Pilot
records had nonzero source sequence/sample time. All 3,196 camera/draw pairs
used the same display frame with no negative stage result; their p95/maximum
skew was 0.524/1.325 ms. Test comparison focus matched the actual pilot pose.
Presenter and trace cache-busy counts were zero. Local exhaust
transformed 419,681 emissions; all four unavailable/stale/publication/identity
skip counters were zero. Source samples had a maximum 90 ms interval;
the exact wrapper's accepted-pose interval maximum was 173 ms, and sampled
presentation sequence changes reached 162 ms. The display buffer and smooth
native frames do not imply a uniform 50 ms authoritative update interval.

The joining pilot used seven native addresses. The initial world 2 establishes
command block ID 2013271749 and faction 20008; replacements at worlds 8 and 14
retain that exact command generation. World 269 changes the command block to
2013277829, matching the successful host remote-respawn event between motion
sequences 1199 and 1200. The following replacements at worlds 270, 276 and 282
retain the new command generation. Thus five address changes are same-command
geometry replacements, while one is a real respawn. None of the sampled
same-command replacements lost its presentation sequence or source time.
The real generation transition must be excluded when comparing uninterrupted
flight derivatives across host/client time.

These numbers describe the full native test duration. The paired recordings
cover an earlier, shorter window and have their own compositor timing limits;
render-pose agreement alone does not certify every camera frame, every network
condition or exact cosmetic plume appearance. Native physics, game actions
and command ownership remain host authoritative throughout this fixture.

The recorded moving window (fixture seconds 2–40, before the real respawn)
had p95 position/angle differences of 2.42 units/0.369 degrees at the declared
100 ms host/client offset. Its aligned p95 client/host linear accelerations
were 408.7/405.7 forward, 401.5/393.3 turning and 236.6/250.1 braking in native
units per second squared. A native asteroid collision occurs in the second
cycle, so these compare gameplay derivatives rather than an isolated free-
flight jitter signal. Neither trace had an expected-velocity stall. Captures
contained 1,675 client and 1,612 host VFR frames, maximum capture interval
50 ms and no grid-signature candidates.

## Commandless replacement continuity

Full-frame review found a remaining nearby grey/green object's orientation or
placement change between client source PTS 31.583 and 31.600 seconds while the
pilot remained coherent. Exact native object identity is not yet assigned.
The nearby world-127 transaction rebuilds five commandless roots solely for
changed subclusters, and ordinary commandless removals previously discarded
their source curves even when their minimum persistent block ID survived.
All top-level neutral roots are included in the fast motion channel; faction
zero alone does not omit their pose.

Replacement tickets now distinguish owned COMMAND generations (kind 1) from
commandless minimum-block anchors (kind 2). Owned validation remains unchanged.
A neutral ticket requires anchor == actor identity, canonical faction zero,
current null-parent root/zone ownership, no locally owned COMMAND, and valid
unique nonzero persistent IDs on every root-owned block. Both old and new
roots are validated twice using a fresh 0x180-byte root header, fresh bounded
pointer-vector copy and one contiguous block header per block. Foreign cached
commands are never promoted into local command generations. Only scalar
identity is retained; no removed root or block pointer is saved in the ticket.
An anchor change, acquired COMMAND, unreadable owner/vector, duplicate ID or
unannounced native lifetime still resets the curve. Legacy individual removal
without the new neutral API uses ordinary removal.

The shared C test retains the identical source curve across reordered/edited
non-anchor blocks and duplicate replay. It rejects stale anchors, changed
owners/parents, owned COMMAND acquisition, malformed/unreadable data and
readable pool reuse, including changes between validation passes. At 4,096
blocks, old detach plus new binding uses exactly 4 * (4,096 + 2) guarded reads;
the original owned-COMMAND and batch-removal tests still pass. The eight
cumulative replacement counters distinguish owned/neutral detach, successful
binding, rejection, unannounced reset and ordinary reset.

The subsequent 120-second moving fixture
(`live-controls-result-1791110750679778400.json`) verified live neutral ticket
usage. Its polled summary records 438 neutral detaches and 438 successful
bindings, six owned detaches/bindings, zero owned/neutral rejections and zero
unannounced resets. The last archived world report contains one additional
neutral application after that summary, bringing both counts to 439. Among
397 retained world reports, 382 neutral continuity rows cover identities
`0x70000000`, `0x70000005`, `0x70000007`, `0x7000000b`, `0x7000001a` and
`0x70000023`; every row has matching before/after minimum anchor, anchor ==
identity and canonical faction zero. Ordinary removals still reset history
(171 recorded resets), rather than preserving actors that left the scene.

The client recorded 7,119 native frame intervals and the matched 60 Hz host
7,280; both had p95/maximum 17/18 ms and zero over 50 ms. The scene gate
completed 4,323 transactions with maximum transaction/render wait
13.174/12.695 ms and zero timeouts. All 3,307 sampled pilot and 1,038 valid
peer records matched intended native render position/angle exactly, with no
zero sequence/sample time. Every sampled camera/draw pair used one frame,
with no negative stage and p95/maximum skew 0.2045/0.5306 ms. The joining pilot's
three addresses retained the same command generation through worlds 2, 8 and
13; no real host respawn occurred. Local exhaust transformed 228,205 emissions
with all four skip counters zero. Source interval maximum was 70 ms; exact
accepted-pose intervals reached 103 ms and sampled sequence changes 133 ms.

This confirms that the tested neutral anchors preserve their curves without
adding native frame hitches in this fixture. It is not a controlled repeat of
identical background geometry, and the two-ship traces do not observe every
asteroid or child body. The earlier grey/green object has no exact native
identity association; these counters alone do not certify that its recorded
visual snap or every background child rotation is resolved.

Attached child poses remain separate. The current planner retains child
position/angle in its structural fingerprint, and fast motion omits roots
with a parent. Exact native `getAbsolutePos` (`0x5d540`) and angle conversion
(`0x8c8f0`) show those raw child fields are parent-relative. `advanceUpdate`
(`0x86c20`) builds world render history from the parent's physical body;
`setRenderPosAngle` (`0x5cae0`) and `advanceRender` (`0x86da0`) update only one
body's history. Preserving a parent curve cannot by itself prove delayed
child-pose coherence; this change does not apply child raw fields as world
coordinates or add unsafe child setters.

## Dense combat after neutral continuity

The following 180-second dense combat fixture
(`coop-check-1791111417145761200.json`) passed functional checks and the
50 ms frame threshold, but contains smaller repeated hitches. Its complete
client histogram has 10,699 intervals, p95 17 ms and maximum 38 ms. Counts
strictly over 18/20/25/33 ms are 58/36/21/3, respectively; zero exceeded 50 ms.
The 21 intervals over 25 ms are 0.196 percent of sampled frames, with clusters
near the first large additions and again near the end of the run. This is not
steady 60 FPS on every frame.

Six consecutive applied geometry intervals (worlds 312, 314, 316, 317, 319
and 321) each add a frame over 25 ms, over approximately 2.1 seconds. Twelve
more such increments occur across worlds 646–681. The worst transaction is
world 675, lasting 29.281 ms with a maximum native render wait of 28.261 ms.
Its recorded stages spend 3 ms removing roots, 19 ms appending and 6 ms in
runtime/visual restoration through binding. The native loader reports
18.4 ms total: 14.8 ms parsing, 3.4 ms expansion and 0.3 ms reading, for
980.6 KB and 213 expanded clusters. These observations support periodic
geometry-import work as a frame-budget problem; they do not isolate neutral
identity validation as the cause.

Neutral detaches and bindings both reached 4,490; owned detaches/bindings
both reached 121. There were zero owned/neutral rejections, unannounced
resets or scene-gate timeouts. Ordinary removals still reset 946 histories.
All 4,569 sampled records for each of the two traced ships match intended
native render position/angle exactly, with nonzero sequence/sample time.
All camera/draw pairs use one frame and report nonnegative stages, with
p95/maximum skew 0.7141/2.3615 ms. The real respawn changes the pilot command
generation at world 42; later replacements at worlds 457, 530, 552 and 562
preserve that generation. Health checks compare 28,411 blocks with zero error.
Local exhaust transforms 772,485 emissions with all four skip counters zero.
These ship/counter results do not certify every background or child body.

Authoritative acceptance has a separate unresolved dense-load interval.
The 374 ms source gap between sequences 170 and 171 contains explicit remote
destruction/respawn, and a 301 ms source gap at 180–181 crosses the host menu
close. During ordinary play, however, the client accepts sequence 1340 at
wall 1791111498684 ms and 1347 at 1791111499089 ms: a 405 ms gap. The host
continues producing intervening samples at 50–94 ms intervals. The following
geometry-303 transaction starts after fresh acceptance and takes 16 ms, so
one long held geometry transaction cannot explain that gap. Its packet/gate
opportunity path was investigated separately. Prepared pending sequences
1342–1346 are present during the gap, while applied sequence remains 1340 and
the owner continues its native player-update callbacks. Transport and packet
preparation are active. This supports repeated failed nonblocking admission
to short idle windows, rather than one long import or absent source packets.
Zero cosmetic stale skips and smooth frame presentation alone do not prove
uniformly fresh simulation state when the controlled ship is hovering.

## Bounded fair idle handoff

The owner may request a reserved opportunity only when its oldest still-
unserved pending motion has waited at least 75 ms. Superseding a pending frame
does not restart that age. RPC preparation records age but does not call native
admission APIs. The next completed draw opens explicit RESERVED state 4 instead
of ordinary IDLE, and the owning update thread may claim it nonblocking.
Before its next Render enters native engine locks, the renderer offers an
unclaimed reservation a 2 ms requested wait budget. Windows scheduling can
exceed that wall time; the separate statistics retain actual maximum wait.

A successful RESERVED-to-MUTATING claim uses the existing commit protocol.
An unclaimed reservation may be abandoned only by a winning CAS from RESERVED
to RENDERING; it has made no object writes and is nonfatal. If the source wins
that race, rendering must wait for the claimed writer's commit under the
existing 100 ms fail-closed rule. A reservation miss retains the request for
another frame; a successful ordinary/reserved claim or true pending-queue
reset clears it. Bootstrap/disabled behavior and the ordinary idle fast path
remain. The existing auto-reset event wakes claims and commits, but only state
checks/CAS grant access, so stale signals do not grant concurrent mutation.

The production-shared C handoff test covers wrong zone/thread, request
coalescing/reset, ordinary and reserved claims, stale event signals, early
claim-and-commit, source wins at the abandonment CAS, failed unclaimed event
wait and the distinction between nonfatal reservation expiry and a claimed
100 ms/uncommitted writer failure. It and the existing scene-gate regression
passed. The separate six counters report requests, reservations, claims,
abandonments, maximum actual reservation wait and whether a request remains.
The subsequent moving and combat fixtures validate bounded live handoffs, as
reported below. Native geometry parsing and attached child-pose coherence
remain separate from this scheduling change.

## Final fair-handoff validation

The 120-second moving fixture
(`live-controls-result-1791113407392586100.json`) and the subsequent 180-second
combat fixture (`coop-check-1791113791155426300.json`) both passed their native
functional and frame-threshold checks. The complete retained client frame
histograms give the following counts; threshold columns mean strictly greater
than the named duration, not greater than or equal to it.

| Fixture | Native intervals | p95 / maximum ms | >18 ms | >20 ms | >25 ms | >33 ms | >50 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Moving client | 7,103 | 17 / 29 | 10 | 6 | 1 | 0 | 0 |
| Combat client | 10,698 | 17 / 23 | 17 | 8 | 0 | 0 | 0 |

The moving host was explicitly paced at 60 Hz for comparison: 7,294 intervals,
p95/maximum 17/18 ms. The combat host was uncapped: 43,609 native intervals,
p95/maximum 5/20 ms. These are actual native frame histograms rather than
motion-trace sampling rates or recorder FPS. The moving client's lone 29 ms
interval already appears in the first retained world report (world 47,
frame 651); the archive no longer contains its precise native stage, so its
cause cannot be assigned from the cumulative maximum. No later moving frame
exceeded 25 ms. Neither fixture proves 60 FPS on every frame.

Moving completed 4,088 scene transactions, with maximum transaction/render
wait 18.629/17.070 ms and zero scene timeouts. Its four requests produced four
reservations and four claims, no abandonment, maximum actual reservation wait
0.349 ms and no remaining request. One request covers initial bootstrap; the
three active requests were accepted 4, 16 and 4 ms after request emission.
Their oldest pending ages at acceptance were 88.8, 94.0 and 88.6 ms. The largest
logged acceptance interval was 155 ms; the complete delivery interval timer
reports 154 ms because it samples another point in the same apply path.
Host source intervals reached 88 ms. The large 1,651.7 ms cumulative pending
age is the pre-first-acceptance bootstrap queue, not a moving-game stall.

Combat completed 6,051 transactions, with maximum transaction/render wait
16.011/14.561 ms and zero scene timeouts. Four requests produced seven
reservations, four successful claims and three nonfatal abandonments, with
no request left pending. Maximum actual reservation wait was 3.159 ms: the
2 ms value is a requested Windows wait budget, not a guaranteed wall-time
bound. The profiles retain aggregate reservation counters, not individual
reservation timestamps; the three abandonments cannot be assigned to a
particular request or missed owner opportunity. The worst recorded
transaction maximum is established at world 658;
its loader reports 9.4 ms total, including 7.4 ms parsing and 1.8 ms expansion
of 487.4 KB. Its stage timestamps span 3 ms removal, 9 ms append and 3 ms
runtime restoration before binding. This retains native parsing inside the
transaction and does not make unrestricted loader work safe outside it.

Admission events separate lifecycle gaps from fresh pending work. Combat's
367 ms accepted interval (sequences 173–174) matches a 367 ms host source gap
containing explicit destruction and respawn. Its 309 ms accepted interval
(183–184) crosses host menu close and a 312 ms source gap. The 217 ms accepted
interval (125–128), with oldest pending age 158.4 ms, occurs during the client's
TAB1 close/TAB16 open transition, while TAB16 is still open. Its request is
emitted 67 ms before acceptance and the host continues producing intervening
samples. It is not an uninterrupted flight interval. Bootstrap accounts for
the cumulative 1,681.7 ms pending maximum.

Every accepted interval of at least 150 ms is emitted by the sparse admission
diagnostic. Those three lifecycle cases are the only such combat events;
ordinary post-menu intervals are therefore below 150 ms in this fixture.
The largest exposed ordinary event is 141 ms (199–201), oldest pending age
103.5 ms, with acceptance 16 ms after its request. A later active request
(2768) is likewise accepted after 16 ms, with a 130 ms acceptance interval.
The earlier ordinary 405 ms fresh-pending starvation is not reproduced here.
This is evidence for the bounded handoff under these fixtures, not a hard
network-latency guarantee. The newer optional admission summary distinguishes
bootstrap from active pending age; the archived reports above still combine
them in `longestPendingAgeMs`.

Moving records 3,137 valid joining-pilot poses and 371 valid peer poses;
combat records 4,581 valid poses for each ship. Every valid record matches
its intended native render position and wrapped angle exactly, with no zero
sample/sequence or sequence rewind. All 3,137 moving and 4,581 combat sampled
camera/draw pairs use the same display frame, report nonnegative stages and
have p95/maximum stage skew 0.5718/1.5650 ms and 0.5515/1.8981 ms respectively.
These are bounded two-ship observations, not measurements of every background
or attached child body. The moving peer is absent from many trace samples;
missing identity rows are excluded rather than counted as a stationary ship.

The moving pilot's world-8 pointer replacement retains the exact owned command
generation, with no actual pilot respawn. Combat changes its command block
from 2013272244 to 2013272484 at world 42, corresponding to the explicit host
respawn; later retained world reports keep that new pointer. Moving records
963 successful neutral detach/bind pairs and four owned pairs; combat records
4,192 neutral pairs and 20 owned pairs. Both have zero replacement rejection
or unannounced reset. Ordinary removal still resets history (327 moving,
485 combat). All four local-exhaust skip counters are zero, across 283,395
moving and 777,487 combat transformations. Combat compares 29,902 block-health
values with zero error.

Original-frame review inspected four moving spans covering forward, turning
and braking and three late combat spans with ships, NPCs and beams. It found
no obvious hull or inspected background snap; grid-signature detections were
zero in both sides' recordings. The moving fixture collides with native
asteroids after about 11 seconds, and late combat mostly hovers, so these
recordings do not constitute constant-velocity free flight or an exact repeat
of the unidentified earlier grey/green child-object snap. Captures are VFR,
with maximum 50 ms capture intervals; they are separate from native frame
histograms. Client exhaust still sometimes appears sparser or dotted during
turns. Particle appearance, all-background child coherence and unrestricted
network conditions remain unverified, despite the observed native body,
camera, continuity and admission improvements.
