"""
drivers/imu_mpu6050.py — MPU-6050 6-DoF IMU Driver (I2C)
Provides accelerometer (±2g) and gyroscope (±250°/s) data.
Drain FIFO at 50Hz via async task.
"""
import asyncio
import struct
import logging
import smbus2
from config import I2C_BUS, IMU_I2C_ADDR, IMU_INT_PIN, IMU_SAMPLE_HZ
from event_bus import bus, IMUData
import RPi.GPIO as GPIO

logger = logging.getLogger(__name__)

# MPU-6050 register map
_PWR_MGMT_1 = 0x6B
_SMPLRT_DIV = 0x19
_CONFIG      = 0x1A
_GYRO_CFG   = 0x1B
_ACCEL_CFG  = 0x1C
_INT_ENABLE = 0x38
_ACCEL_XOUT = 0x3B
_TEMP_OUT   = 0x41
_GYRO_XOUT  = 0x43
_WHO_AM_I   = 0x75

ACCEL_SCALE = 2.0 / 32768.0    # ±2g
GYRO_SCALE  = 250.0 / 32768.0  # ±250°/s


class MPU6050:
    def __init__(self):
        self._bus: smbus2.SMBus | None = None
        self._running = False

    def init(self) -> None:
        self._bus = smbus2.SMBus(I2C_BUS)
        who = self._bus.read_byte_data(IMU_I2C_ADDR, _WHO_AM_I)
        if who not in (0x68, 0x70, 0x72):
            raise RuntimeError(f"MPU-6050 not found (WHO_AM_I=0x{who:02X})")
        # Wake up (clear sleep bit)
        self._bus.write_byte_data(IMU_I2C_ADDR, _PWR_MGMT_1, 0x00)
        # Set sample rate divider (1kHz / (1+div) = target rate)
        div = int(1000 / IMU_SAMPLE_HZ) - 1
        self._bus.write_byte_data(IMU_I2C_ADDR, _SMPLRT_DIV, div)
        # DLPF config = 3 (44Hz bandwidth)
        self._bus.write_byte_data(IMU_I2C_ADDR, _CONFIG, 0x03)
        # Gyro ±250°/s, Accel ±2g
        self._bus.write_byte_data(IMU_I2C_ADDR, _GYRO_CFG,  0x00)
        self._bus.write_byte_data(IMU_I2C_ADDR, _ACCEL_CFG, 0x00)
        logger.info(f"[MPU6050] Ready @ I2C 0x{IMU_I2C_ADDR:02X}, {IMU_SAMPLE_HZ}Hz")

    def read_raw(self) -> tuple:
        """Returns (ax,ay,az,gx,gy,gz) in SI units (m/s², °/s)."""
        data = self._bus.read_i2c_block_data(IMU_I2C_ADDR, _ACCEL_XOUT, 14)
        ax, ay, az = struct.unpack_from(">hhh", bytes(data), 0)
        gx, gy, gz = struct.unpack_from(">hhh", bytes(data), 8)
        return (
            (ax*ACCEL_SCALE*9.81, ay*ACCEL_SCALE*9.81, az*ACCEL_SCALE*9.81),
            (gx*GYRO_SCALE,       gy*GYRO_SCALE,       gz*GYRO_SCALE),
        )

    async def run(self) -> None:
        import time
        self._running = True
        interval = 1.0 / IMU_SAMPLE_HZ
        while self._running:
            try:
                accel, gyro = self.read_raw()
                await bus.publish(IMUData(
                    accel=accel, gyro=gyro, timestamp=time.monotonic()
                ))
            except Exception as exc:
                logger.error(f"[MPU6050] Read error: {exc}")
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self.stop()
        if self._bus:
            self._bus.close()


imu = MPU6050()
