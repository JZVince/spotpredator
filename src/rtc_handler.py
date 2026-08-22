"""RTC Handler for DS3231 Real-Time Clock Module"""
import logging
import subprocess
import time
from datetime import datetime
try:
    import smbus2
except ImportError:
    smbus2 = None

logger = logging.getLogger(__name__)


class RTCHandler:
    """Simple interface for DS3231 RTC module"""

    def __init__(self, i2c_address=0x68, bus_number=1):
        """
        Initialize RTC handler

        Args:
            i2c_address: I2C address of DS3231 (default 0x68)
            bus_number: I2C bus number (default 1 for Pi)
        """
        self.i2c_address = i2c_address
        self.bus_number = bus_number
        self.bus = None
        self.rtc_available = False

        if smbus2 is None:
            logger.warning("smbus2 not available, using system time")
            return

        try:
            self.bus = smbus2.SMBus(bus_number)
            # Try to read from RTC to verify it's working
            self._read_time()
            self.rtc_available = True
            logger.info("RTC initialized successfully")
        except Exception as e:
            logger.warning(f"RTC not available, using system time: {e}")
            self.rtc_available = False

    # DS3231 status register (0x0F), bit 7 = OSF (Oscillator Stop Flag). The chip sets OSF whenever
    # its oscillator has stopped since OSF was last cleared — i.e. it lost ALL power (main + backup
    # battery) and its kept time is no longer valid. Reading a plausible year is NOT enough: a
    # power-lost RTC can show a believable-but-wrong time. We treat OSF=1 as "do not trust the RTC".
    _STATUS_REG = 0x0F
    _OSF_BIT = 0x80

    def osf_is_set(self):
        """True if the DS3231 OSF flag is set (oscillator stopped since last clear → time invalid).
        Returns False if the RTC is unavailable or the read fails (caller falls back on year guard)."""
        if not self.rtc_available or not self.bus:
            return False
        try:
            status = self.bus.read_byte_data(self.i2c_address, self._STATUS_REG)
            return bool(status & self._OSF_BIT)
        except Exception as e:
            logger.debug(f"Failed to read RTC status (OSF): {e}")
            return False

    def clear_osf(self):
        """Clear the OSF flag (write 0 to bit 7 of the status reg), preserving other bits. Called
        after we set a known-good time, so a future OSF=1 unambiguously means 'lost power since'."""
        if not self.rtc_available or not self.bus:
            return False
        try:
            status = self.bus.read_byte_data(self.i2c_address, self._STATUS_REG)
            self.bus.write_byte_data(self.i2c_address, self._STATUS_REG, status & ~self._OSF_BIT)
            return True
        except Exception as e:
            logger.error(f"Failed to clear RTC OSF flag: {e}")
            return False

    def _bcd_to_dec(self, bcd):
        """Convert BCD to decimal"""
        return ((bcd // 16) * 10) + (bcd % 16)

    def _dec_to_bcd(self, dec):
        """Convert decimal to BCD"""
        return ((dec // 10) * 16) + (dec % 10)

    def _read_time(self):
        """Read time from RTC"""
        if not self.rtc_available or not self.bus:
            return None

        try:
            # Read 7 bytes starting from register 0x00
            data = self.bus.read_i2c_block_data(self.i2c_address, 0x00, 7)

            second = self._bcd_to_dec(data[0] & 0x7F)
            minute = self._bcd_to_dec(data[1])
            hour = self._bcd_to_dec(data[2] & 0x3F)
            day = self._bcd_to_dec(data[4])
            month = self._bcd_to_dec(data[5] & 0x1F)
            year = self._bcd_to_dec(data[6]) + 2000

            return datetime(year, month, day, hour, minute, second)

        except Exception as e:
            logger.debug(f"Failed to read from RTC: {e}")
            return None

    def set_time(self, dt=None):
        """
        Write time to RTC. Uses current system time if dt not provided.
        """
        if not self.rtc_available or not self.bus:
            logger.warning("RTC not available, cannot set time")
            return False
        if dt is None:
            dt = datetime.now()
        try:
            data = [
                self._dec_to_bcd(dt.second),
                self._dec_to_bcd(dt.minute),
                self._dec_to_bcd(dt.hour),
                self._dec_to_bcd(dt.weekday() + 1),
                self._dec_to_bcd(dt.day),
                self._dec_to_bcd(dt.month),
                self._dec_to_bcd(dt.year - 2000),
            ]
            self.bus.write_i2c_block_data(self.i2c_address, 0x00, data)
            # We just wrote a known-good time, so any prior "lost power" condition is resolved —
            # clear OSF so a future OSF=1 unambiguously flags a NEW power loss (dead/loose battery).
            self.clear_osf()
            logger.info(f"RTC time set to {dt}")
            return True
        except Exception as e:
            logger.error(f"Failed to set RTC time: {e}")
            return False

    def get_time(self):
        """
        Get current time from the RTC. We trust the RTC because sync_time() reconciles it against
        the system clock at STARTUP: online it's corrected from NTP, offline the system clock was
        set from it — so by the time the loop runs, the RTC is accurate either way. (We don't ping
        for connectivity on every call — that would be far too slow; the reconciliation happens
        once at startup.) Minimal guard: if the RTC reads an impossible year (< 2024, uninitialized
        chip), fall back to the system clock.

        Returns:
            datetime: RTC time (trusted), or system time only if the RTC is absent/uninitialized/OSF.
        """
        if self.rtc_available:
            # OSF=1 means the RTC lost all power (main + backup) since we last set it, so its time is
            # not trustworthy even if the year looks plausible — fall back to the system clock. This
            # is the safeguard for a dead/loose backup battery (the 2026-08 loose-GND failure): it
            # surfaces the problem instead of silently timestamping everything with a wrong RTC time.
            if self.osf_is_set():
                logger.warning("⚠️  RTC OSF flag set — RTC lost power (check backup battery/GND). "
                               "Not trusting RTC time; using system clock.")
                return datetime.now()
            rtc_time = self._read_time()
            if rtc_time and rtc_time.year >= 2024:
                return rtc_time
            if rtc_time:
                logger.warning(f"RTC time {rtc_time} has an invalid year — using system time.")
        return datetime.now()

    @staticmethod
    def _has_internet():
        """True if the internet is reachable."""
        try:
            r = subprocess.run(["ping", "-c", "1", "-W", "3", "8.8.8.8"],
                               capture_output=True, timeout=6)
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _ntp_synchronized():
        """True ONLY if the system clock has ACTUALLY been synced by NTP — not merely 'online'.

        This is the fix for the 2026-08-19 bug: being online is NOT the same as the system clock
        being correct. At startup the Pi can have internet while NTP hasn't caught up yet, so the
        system clock is still stale (it came up from the RTC/last-known and hasn't been corrected).
        Trusting mere connectivity then let a stale system clock (10:43) OVERWRITE a MORE-correct
        RTC (10:52). timedatectl's NTPSynchronized flag is the real 'the clock is trustworthy now'
        signal — it is only 'yes' once NTP has genuinely disciplined the clock."""
        try:
            r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                               capture_output=True, text=True, timeout=6)
            return r.stdout.strip().lower() in ("yes", "true", "1")
        except Exception:
            return False

    def _wait_for_ntp(self, max_wait=45, poll=3):
        """If we're online, give NTP a brief bounded window to finish syncing before deciding, so a
        boot-with-WiFi actually corrects the RTC that boot instead of missing it because NTP was a
        few seconds late. Returns True as soon as NTP is synced, or False after max_wait. Offline
        boots skip the wait entirely (no point waiting for NTP with no network)."""
        if self._ntp_synchronized():
            return True
        if not self._has_internet():
            return False  # offline — don't waste startup time waiting for NTP that can't happen
        logger.info(f"⏳ Online but NTP not synced yet — waiting up to {max_wait}s for it...")
        waited = 0
        while waited < max_wait:
            time.sleep(poll)
            waited += poll
            if self._ntp_synchronized():
                logger.info(f"✅ NTP synced after ~{waited}s.")
                return True
        logger.warning(f"NTP still not synced after {max_wait}s — proceeding with RTC as source of truth.")
        return False

    def sync_system_from_rtc(self):
        """Set the OS system clock FROM the RTC (used when OFFLINE). Returns True on success."""
        if not self.rtc_available:
            logger.info("RTC not available — leaving system clock as-is.")
            return False
        if self.osf_is_set():
            logger.warning("⚠️  RTC OSF set (lost power — check backup battery/GND) — not syncing "
                           "system clock from an untrustworthy RTC.")
            return False
        rtc_time = self._read_time()
        if not rtc_time or rtc_time.year < 2024:
            logger.warning(f"RTC time invalid ({rtc_time}) — not syncing system clock from it.")
            return False
        stamp = rtc_time.strftime("%m%d%H%M%Y.%S")  # `date` format: MMDDhhmmYYYY.ss
        try:
            subprocess.run(["sudo", "date", stamp], check=True,
                           capture_output=True, text=True, timeout=10)
            logger.info(f"🕐 System clock set from RTC: {rtc_time}")
            return True
        except Exception as e:
            logger.error(f"Failed to set system clock from RTC: {e}")
            return False

    def sync_time(self):
        """Reconcile the system clock and the RTC at startup, gated on whether NTP has ACTUALLY
        synchronized the system clock — NOT merely on whether there's internet.

        - NTP-SYNCED (system clock genuinely disciplined by NTP → trustworthy): TRUST the system
          clock and WRITE IT TO THE RTC, correcting any RTC drift for the next offline reboot.
        - NOT NTP-SYNCED (offline, OR online-but-NTP-hasn't-caught-up-yet → system clock may be
          stale): TRUST THE RTC and set the system clock from it. Do NOT overwrite the RTC with an
          unverified system clock.

        Why gate on NTP-sync, not connectivity (2026-08-19 fix): at boot the Pi can be ONLINE while
        the system clock is still stale (NTP not yet synced). The old code trusted 'online' and
        overwrote a MORE-correct RTC (10:52) with a stale system clock (10:43). NTPSynchronized is
        the real 'clock is correct now' signal. Call once at startup, before using time.
        """
        # Give NTP a brief bounded window to finish (only waits if online-but-not-yet-synced), so a
        # WiFi boot corrects the RTC this boot instead of missing it by a few seconds.
        if self._wait_for_ntp():
            # System clock is genuinely NTP-accurate → correct the RTC from it. If OSF was set (RTC
            # had lost power), set_time() below clears it — so an NTP-synced boot self-heals a
            # battery/GND blip. Logged so a recurring OSF every boot is visible as a real hw fault.
            if self.osf_is_set():
                logger.warning("⚠️  RTC OSF was set (lost power since last set) — NTP-synced now, "
                               "re-setting RTC from system clock and clearing OSF. If this recurs "
                               "every boot, the backup battery/GND is faulty.")
            sys_time = datetime.now()
            logger.info(f"🌐 NTP-synced — trusting system clock ({sys_time}); updating RTC from it.")
            self.set_time(sys_time)
            return "ntp"
        else:
            # NOT NTP-synced (offline, or NTP hasn't caught up yet) — the RTC is the source of truth.
            # Push it to the system clock. This protects the RTC's good time from being clobbered by
            # a not-yet-corrected system clock at boot.
            reachable = self._has_internet()
            logger.info(f"📴 System clock NOT NTP-synced ({'online but NTP not ready' if reachable else 'offline'}) "
                        "— trusting RTC; setting system clock from it.")
            self.sync_system_from_rtc()
            return "rtc"

    def get_timestamp_string(self, fmt="%Y-%m-%d %H:%M:%S"):
        """
        Get formatted timestamp string

        Args:
            fmt: strftime format string

        Returns:
            str: Formatted timestamp
        """
        return self.get_time().strftime(fmt)

    def __del__(self):
        """Cleanup"""
        if self.bus:
            try:
                self.bus.close()
            except Exception:
                pass


if __name__ == "__main__":
    # Simple test
    logging.basicConfig(level=logging.INFO)

    print("Testing RTC...")
    rtc = RTCHandler()

    print(f"RTC available: {rtc.rtc_available}")

    current_time = rtc.get_time()
    print(f"Current time: {current_time}")

    timestamp = rtc.get_timestamp_string()
    print(f"Formatted: {timestamp}")

    if rtc.rtc_available:
        print("✅ RTC working")
    else:
        print("⚠️  Using system time (RTC not detected)")
