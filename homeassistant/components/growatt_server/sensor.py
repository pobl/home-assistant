"""Read status of growatt inverters."""
import re
import json
import logging
import datetime
from collections import namedtuple

import growattServer
import voluptuous as vol

from homeassistant.util import Throttle
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import CONF_NAME, CONF_USERNAME, CONF_PASSWORD, ATTR_TEMPERATURE, CONF_API_KEY, CONF_NAME, ATTR_DATE, ATTR_TIME, ATTR_VOLTAGE

_LOGGER = logging.getLogger(__name__)
ATTR_POWER_GENERATION = 'power_generation'
CONF_PLANT_ID = "plant_id"
DEFAULT_PLANT_ID = "0"
DEFAULT_NAME = "Growatt"
SCAN_INTERVAL = datetime.timedelta(minutes=5)

TOTAL_SENSOR_TYPES = {
    "total_money_today": ("Total money today", "€", "plantMoneyText", None),
    "total_money_total": ("Money lifetime", "€", "totalMoneyText", None),
    "total_energy_today": ("Energy Today", "kWh", "todayEnergy", "power"),
    "total_output_power": ("Output Power", "W", "invTodayPpv", "power"),
    "total_energy_output": ("Lifetime energy output", "kWh", "totalEnergy", "power"),
    "total_maximum_output": ("Maximum power", "W", "nominalPower", "power"),
}

INVERTER_SENSOR_TYPES = {
    "inverter_energy_today": ("Energy today", "kWh", "e_today", "power"),
    "inverter_energy_total": ("Lifetime energy output", "kWh", "e_total", "power"),
    "inverter_voltage_input_1": ("Input 1 voltage", "V", "vpv1", None),
    "inverter_amperage_input_1": ("Input 1 Amperage", "A", "ipv1", None),
    "inverter_wattage_input_1": ("Input 1 Wattage", "W", "ppv1", "power"),
    "inverter_voltage_input_2": ("Input 2 voltage", "V", "vpv2", None),
    "inverter_amperage_input_2": ("Input 2 Amperage", "A", "ipv2", None),
    "inverter_wattage_input_2": ("Input 2 Wattage", "W", "ppv2", "power"),
    "inverter_voltage_input_3": ("Input 3 voltage", "V", "vpv3", None),
    "inverter_amperage_input_3": ("Input 3 Amperage", "A", "ipv3", None),
    "inverter_wattage_input_3": ("Input 3 Wattage", "W", "ppv3", "power"),
    "inverter_internal_wattage": ("Internal wattage", "W", "ppv", "power"),
    "inverter_reactive_voltage": ("Reactive voltage", "V", "vacr", None),
    "inverter_inverter_reactive_amperage": ("Reactive amperage", "A", "iacr", None),
    "inverter_frequency": ("AC frequency", "Hz", "fac", None),
    "inverter_current_wattage": ("Output power", "W", "pac", "power"),
    "inverter_current_reactive_wattage": ("Reactive wattage", "W", "pacr", "power"),
}

SENSOR_TYPES = {**TOTAL_SENSOR_TYPES, **INVERTER_SENSOR_TYPES}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_PLANT_ID, default=DEFAULT_PLANT_ID): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Growatt sensor."""
    username = config[CONF_USERNAME]
    password = config[CONF_PASSWORD]
    plant_id = config[CONF_PLANT_ID]
    name = config[CONF_NAME]
    api = growattServer.GrowattApi()
    

    # Log in to api and fetch first plant if no plant id is defined.
    login_response = api.login(username, password)
    if not login_response["success"] and login_response["errCode"] == "102":
        _LOGGER.error("Username or Password may be incorrect!")
        return
    user_id = login_response["userId"]
    if plant_id == DEFAULT_PLANT_ID:
        plant_info = api.plant_list(user_id)
        plant_id = plant_info["data"][0]["plantId"]
    
    add_entities([GrowatttSensor(api, 'Growatt_Battery', username, password)], True)

    # Get a list of inverters for specified plant to add sensors for.
    inverters = api.inverter_list(plant_id)
    entities = []
    probe = GrowattData(api, username, password, plant_id, True)
    for sensor in TOTAL_SENSOR_TYPES:
        entities.append(
            GrowattInverter(probe, f"{name} Total", sensor, f"{plant_id}-{sensor}")
        )

        # Add sensors for each inverter in the specified plant.
    for inverter in inverters:
        probe = GrowattData(api, username, password, inverter["deviceSn"], False)
        for sensor in INVERTER_SENSOR_TYPES:
            entities.append(
                GrowattInverter(
                    probe,
                    f"{inverter['deviceAilas']}",
                    sensor,
                    f"{inverter['deviceSn']}-{sensor}",
                )
            )
    add_entities(entities, True)

class GrowattInverter(Entity):
    """Representation of a Growatt Sensor."""

    def __init__(self, probe, name, sensor, unique_id):
        """Initialize a PVOutput sensor."""
        self.sensor = sensor
        self.probe = probe
        self._name = name
        self._state = None
        self._unique_id = unique_id

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._name} {SENSOR_TYPES[self.sensor][0]}"

    @property
    def unique_id(self):
        """Return the unique id of the sensor."""
        return self._unique_id

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return "mdi:solar-power"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self.probe.get_data(SENSOR_TYPES[self.sensor][2])

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return SENSOR_TYPES[self.sensor][3]

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return SENSOR_TYPES[self.sensor][1]

    def update(self):
        """Get the latest data from the Growat API and updates the state."""
        self.probe.update()


class GrowattData:
    """The class for handling data retrieval."""

    def __init__(self, api, username, password, inverter_id, is_total=False):
        """Initialize the probe."""

        self.is_total = is_total
        self.api = api
        self.inverter_id = inverter_id
        self.data = {}
        self.username = username
        self.password = password

    @Throttle(SCAN_INTERVAL)
    def update(self):
        """Update probe data."""
        self.api.login(self.username, self.password)
        _LOGGER.debug("Updating data for %s", self.inverter_id)
        try:
            if self.is_total:
                total_info = self.api.plant_info(self.inverter_id)
                del total_info["deviceList"]
                # PlantMoneyText comes in as "3.1/€" remove anything that isn't part of the number
                total_info["plantMoneyText"] = re.sub(
                    r"[^\d.,]", "", total_info["plantMoneyText"]
                )
                self.data = total_info
            else:
                inverter_info = self.api.inverter_detail(self.inverter_id)
                self.data = inverter_info["data"]
        except json.decoder.JSONDecodeError:
            _LOGGER.error("Unable to fetch data from Growatt server")

    def get_data(self, variable):
        """Get the data."""
        return self.data.get(variable)
class GrowatttSensor(Entity):
    """Representation of a Growattt Sensor."""

    def __init__(self, api, name, u, p):
        """Initialize a PVOutput sensor."""
        self.api = api
        self._name = name
        self.username = u
        self.password = p
        self._state = None
        self._unit_of_measurement = '%'
        self.totalPowerToday = '0'
        self.status = namedtuple(
            'status', [ATTR_DATE, ATTR_TIME,
                       ATTR_POWER_GENERATION,
                       ATTR_TEMPERATURE, ATTR_VOLTAGE])

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def icon(self):
        """Return the state of the sensor."""
        return 'mdi:car-battery'

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state
    
    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return self._unit_of_measurement

    def update(self):
        """Get the latest data from the Growat API and updates the state."""
        try:
            login_res = self.api.login(self.username, self.password)
            user_id = login_res['userId']
            plant_info = self.api.plant_list(user_id)
            battery = plant_info['data'][0]['storageCapacity']
            data = battery.replace('%', '')
            self._state = data
        except TypeError:
            _LOGGER.error(
                "Unable to fetch data from Growatt server. %s")   
