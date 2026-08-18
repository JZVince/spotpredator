"""RTC Handler for DS3231 Real-Time Clock Module"""
import logging
import subprocess
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
        """True if the internet is reachable (so the system clock is NTP-synced and trustworthy)."""
        try:
            r = subprocess.run(["ping", "-c", "1", "-W", "3", "8.8.8.8"],
                               capture_output=True, timeout=6)
            return r.returncode == 0
        except Exception:
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
        """Reconcile the system clock and the RTC at startup, based on WiFi/internet state:

        - ONLINE  (WiFi + internet → system clock is NTP-accurate): TRUST the system clock and
          WRITE IT TO THE RTC, so the RTC is corrected for the next offline reboot.
        - OFFLINE (no internet → system clock may be stale after a field reboot): TRUST the RTC and
          SET THE SYSTEM CLOCK FROM IT.

        This keeps the RTC accurate (fixed whenever WiFi is around) and keeps system time correct
        offline (derived from the RTC). Call once at startup, before using time.
        """
        if self._has_internet():
            # System clock is trustworthy (NTP). Correct the RTC from it. If OSF was set (RTC had
            # lost power), set_time() below clears it — so being online self-heals a battery/GND
            # blip. We still log it so a recurring OSF at every boot is visible as a real hw fault.
            if self.osf_is_set():
                logger.warning("⚠️  RTC OSF was set (lost power since last set) — online now, "
                               "re-setting RTC from NTP and clearing OSF. If this recurs every "
                               "boot, the backup battery/GND is faulty.")
            sys_time = datetime.now()
            logger.info(f"🌐 Online — trusting system clock ({sys_time}); updating RTC from it.")
            self.set_time(sys_time)
            return "online"
        else:
            # Offline — RTC is the source of truth. Push it to the system clock.
            logger.info("📴 Offline — trusting RTC; setting system clock from it.")
            self.sync_system_from_rtc()
            return "offline"

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
