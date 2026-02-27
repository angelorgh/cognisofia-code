//
#include <SPI.h> //SPI communication
#include <Wire.h> // Wire.h
#include <Adafruit_MCP4728.h>
#include <SparkFun_MAX1704x_Fuel_Gauge_Arduino_Library.h>
#include <EEPROM.h>
#include <CD74HC4067.h>

// GPIO driver variable
byte gpioChipAddr = 0x27;

// Define object for lipo fuel gauge
SFE_MAX1704X lipo(MAX1704X_MAX17043); // Create a MAX17043
bool alert; // Variable to keep track of whether alert has been triggered

// DAC control variables or objects
Adafruit_MCP4728 mcp;

// Variables for communication with ADS1256 chip via SPI
//Variables
double VREF = 2.5; //Value of V_ref. In case of internal V_ref, it is 2.5 V
double voltage = 0; //Converted RAW bits. 
int CS_Value; //we use this to store the value of the bool, since we don't want to directly modify the CS_pin

//Pins
const byte CS_pin1 = 4;	//goes to CS on ADS1256
const byte DRDY_pin1 = 3;  //goes to DRDY on ADS1256
const byte RESET_pin1 = 5; //goes to RST on ADS1256 

const byte CS_pin2 = 7;	//goes to CS on ADS1256
const byte DRDY_pin2 = 6;  //goes to DRDY on ADS1256
const byte RESET_pin2 = 8; //goes to RST on ADS1256 

// Multiplexer on/off control
const byte MUX_EN_PIN = 23;

bool chargerConnected = false;

// 
const byte BATTERY_CHARGER_STATUS = 10;

//const byte PDWN_PIN = PA1; //Goes to the PDWN/SYNC/RESET pin (notice, that some boards have different pins!)
//The above pins are described for STM32. For Arduino, you have to use a different pin definition ("PA" is not used in "PA4)

//Values for registers
uint8_t registerAddress; //address of the register, both for reading and writing - selects the register
uint8_t registerValueR; //this is used to READ a register
uint8_t registerValueW; //this is used to WRTIE a register
int32_t registerData; //this is used to store the data read from the register (for the AD-conversion)
uint8_t directCommand; //this is used to store the direct command for sending a command to the ADS1256
String ConversionResults; //Stores the result of the AD conversion
String PrintMessage; //this is used to concatenate stuff into before printing it out. 

// BLE libraries
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <arduino-timer.h>
auto timer = timer_create_default(); // create a timer with default settings

//BLE variables
BLEServer* pServer = NULL;
BLECharacteristic* pDataCharacteristic = NULL;
BLECharacteristic* pDataCharacteristic2 = NULL;
BLECharacteristic* pLEDCharacteristic = NULL;
bool deviceConnected = false;
bool oldDeviceConnected = false;

short batteryPercentage = 0;
int lastTimeStamp = millis();
int intervalTimeStamp = millis();

int s0 = 19; // D10 = GPIO21
int s1 = 20; // D2 = GPIO5 
int s2 = 17; // A7 = GPIO14 = D24
int s3 = 18; // A6 = GPIO13 = D23 // refer https://docs.arduino.cc/resources/pinouts/ABX00083-full-pinout.pdf

CD74HC4067 my_mux(s0, s1, s2, s3);  // create a new CD74HC4067 object with its four control pins

// See the following for generating UUIDs:
// https://www.uuidgenerator.net/

#define fNIRS_SERVICE_UUID        "938548e6-c655-11ea-87d0-0242ac130003"
#define DATA_CHARACTERISTIC_UUID "77539407-6493-4b89-985f-baaf4c0f8d86"
#define DATA_CHARACTERISTIC_UUID2 "513b630c-e5fd-45b5-a678-bb2835d6c1d2"
#define DATA_CHARACTERISTIC_UUID3 "613b680c-e5fd-95b5-a638-bb2233d6c1d7"
#define LED_CHARACTERISTIC_UUID "19B10001-E8F2-537E-4F6C-D104768A1213"

const int NUM_SOURCES = 32 + 1; // regular power light source, low power light source, dark
const int DETECTORS_PER_SOURCE = 16 + 1; // 16 detectors + round duration
const int DATA_SIZE_BYTES = 4; // 24 bits
const int PACKET_SIZE = NUM_SOURCES * DETECTORS_PER_SOURCE; // * DATA_SIZE_BYTES; // 384 bytes for data + 6 bytes for header
int dataPacket[PACKET_SIZE]; // First data packet
int dataPacketToSend[PACKET_SIZE]; // First data packet
int darkCurrentValues[DETECTORS_PER_SOURCE];

boolean readDataBool = false;
int ledIntensities[NUM_SOURCES-1] = {100, 100, 100, 100,
                          100, 100, 100, 100,
                          100, 100, 100, 100,
                          100, 100, 100, 100,
                          50, 50, 50, 50,
                          50, 50, 50, 50,
                          50, 50, 50, 50,
                          50, 50, 50, 50
                          };
                        

int sourceNumber = 0;
int startTime = 0;
int batteryEventCounter = 600;
int datasetTimeInterval = 0;

int lastPacket1 = 0;
int lastPacket2 = 0;
int lastPacket3 = 0;
bool dataReadyToSend = false;
int dataSetCounter = 1;

const int eepromAddress = 0;

class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
      readDataBool = false;
      Serial.println("Connected!!");

        // Turn system LED to BLUE
        digitalWrite(14, HIGH); // Red OFF
        digitalWrite(16, LOW);  // Blue ON
        digitalWrite(15, HIGH); // Green OFF

      // // send LED intensities to connected central      
      // pDataCharacteristic->setValue((uint8_t*)ledIntensities, 64);
      // pDataCharacteristic->notify();

    };

    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
      Serial.println("Disconnected!!");
      mcp.setChannelValue(MCP4728_CHANNEL_A, 0);
    }
};

volatile bool isCharging = false;  // Flag to track charging status

// Interrupt Service Routine (ISR)
void IRAM_ATTR chargingStatusISR() {
    
  // Update the flag based on the current state of the signal
  isCharging = digitalRead(BATTERY_CHARGER_STATUS) == HIGH;

  if(isCharging){
    
    // Turn system LED to YELLOW
    digitalWrite(14, LOW); // Red OFF
    digitalWrite(16, HIGH);  // Blue OFF
    digitalWrite(15, LOW); // Green ON

  }

}


void setup() {

  // set the pin status to input
  pinMode(BATTERY_CHARGER_STATUS, INPUT);

  // Attach interrupt to CHARGING_PIN
  attachInterrupt(digitalPinToInterrupt(BATTERY_CHARGER_STATUS), chargingStatusISR, CHANGE);

  isCharging = digitalRead(BATTERY_CHARGER_STATUS) == HIGH;

  if(isCharging){
    
    // Turn system LED to YELLOW
    digitalWrite(14, LOW); // Red OFF
    digitalWrite(16, HIGH);  // Blue OFF
    digitalWrite(15, LOW); // Green ON

  }

  if (!isCharging){

    Serial.begin(115200);
    setCpuFrequencyMhz(240);
    
    // Setup pin mode
    pinMode(14, OUTPUT);
    pinMode(15, OUTPUT);
    pinMode(16, OUTPUT);

    // Turn system LED on
    digitalWrite(14, HIGH); // Red OFF
    digitalWrite(16, HIGH);  // Blue OFF
    digitalWrite(15, LOW); // Green ON

    // Initialize the EEPROM
    EEPROM.begin(512);

    // // Check if LED intensity data is in EEProm
    // if (areLEDIntensitiesInEEPROM()){
    //   Serial.println("EEPROM: Old LED values found, loading...");
    //   // Load the array from EEPROM
    //   loadLEDIntensitiesFromEEPROM();
    // }
    // else {
    //   Serial.println("EEPROM: empty, no values found!");
    //   // No values found in EEPROM, initialize the array with default values
    //   for (int i = 0; i < 16; i++) {
    //     ledIntensities[i] = 6; // Set default values here
    //   }
    //   // Save the default values to EEPROM
    //   saveLEDIntensitiesToEEPROM();
    // }

    initADCs();

    initSourceDrivers();

    initBatteryFuelGauge();

    initBLEServer();

    timer.every(6, runfNIRSSequence);

    timer.every(40, broadcastBLEData);

  }
  else{
    
    // Turn system LED to YELLOW
    digitalWrite(14, LOW); // Red OFF
    digitalWrite(16, HIGH);  // Blue OFF
    digitalWrite(15, LOW); // Green ON

  }


}

void loop() {

    if (!isCharging){
      timer.tick();

      // disconnecting
      if (!deviceConnected && oldDeviceConnected) {
          delay(500); // give the bluetooth stack the chance to get things ready
          pServer->startAdvertising(); // restart advertising
          Serial.println("start advertising");
          oldDeviceConnected = deviceConnected;
          readDataBool = false;
          
      }

      // connecting
      if (deviceConnected && !oldDeviceConnected) {
          // do stuff here on connecting
          oldDeviceConnected = deviceConnected;
          batteryEventCounter = 200;
      }
    }
    else{
      
      // Turn system LED to YELLOW
      digitalWrite(14, LOW); // Red OFF
      digitalWrite(16, HIGH);  // Blue OFF
      digitalWrite(15, LOW); // Green ON

    }

}

bool areLEDIntensitiesInEEPROM() {
  Serial.println("Checking EEPROM...");
  for (int i = 0; i < 16; i++) {
    if (EEPROM.read(eepromAddress + i) != 255) {
      return true;
    }
  }
  return false;
}

void saveLEDIntensitiesToEEPROM() {
  for (int i = 0; i < 16; i++) {
    EEPROM.write(eepromAddress + i, ledIntensities[i]);
  } 
  EEPROM.commit();  // Commit the changes to EEPROM
}

void loadLEDIntensitiesFromEEPROM() {
  for (int i = 0; i < 16; i++) {
    ledIntensities[i] = EEPROM.read(eepromAddress + i);
  }
}

void sendBatteryLevel(){

  Serial.println("Sending battery level over BLE!");

	// lipo.getSOC() returns the estimated state of charge (e.g. 79%)
  int batteryLevel = 0;
  batteryLevel = lipo.getSOC();
  Serial.print(batteryLevel);
  Serial.println(" is the battery level");
  
  pDataCharacteristic->setValue((uint8_t*)&batteryLevel, sizeof(batteryLevel));
  pDataCharacteristic->notify();

}

void sendLEDIntensities(){
  
  pDataCharacteristic->setValue((uint8_t*)ledIntensities, 64);
  pDataCharacteristic->notify();

}

bool broadcastBLEData(void*){
  
  int dataPacketNumElements = 17*7 + 1;
  int dataPacketNumElements2 = 17*5 + 1;
  int dataPacketSize = dataPacketNumElements*4;
  int dataPacketSize2 = dataPacketNumElements2*4;
  int dataPacketSnippet[dataPacketNumElements];
  int dataPacketSnippet2[dataPacketNumElements2];
  int offset  = 0;

  if (readDataBool && dataReadyToSend){
    if (dataSetCounter == 1){

      dataPacketSnippet[0] = dataSetCounter;
      offset = 0;
      for(int i = 1; i < dataPacketNumElements; i++){
        dataPacketSnippet[i] = dataPacketToSend[i-1+offset];
      }
      sendDataViaBLE(dataPacketSnippet, dataPacketSize);      
      dataSetCounter++;

    }
    else if (dataSetCounter == 2){

      dataPacketSnippet[0] = dataSetCounter;
      offset = 7*17;
      for(int i = 1; i < dataPacketNumElements; i++){
        dataPacketSnippet[i] = dataPacketToSend[i-1+offset];
      }
      sendDataViaBLE2(dataPacketSnippet, dataPacketSize);      
      dataSetCounter++;

    }
    else if (dataSetCounter == 3){

      dataPacketSnippet[0] = dataSetCounter;
      offset = 14*17;
      for(int i = 1; i < dataPacketNumElements; i++){
        dataPacketSnippet[i] = dataPacketToSend[i-1+offset];
      }
      sendDataViaBLE2(dataPacketSnippet, dataPacketSize);      
      dataSetCounter++;

    }
    else if (dataSetCounter == 4){

      dataPacketSnippet[0] = dataSetCounter;
      offset = 21*17;
      for(int i = 1; i < dataPacketNumElements; i++){
        dataPacketSnippet[i] = dataPacketToSend[i-1+offset];
      }
      sendDataViaBLE2(dataPacketSnippet, dataPacketSize);      
      dataSetCounter++;

    }
    else if (dataSetCounter == 5){
      
      dataPacketSnippet2[0] = dataSetCounter;
      offset = 28*17;
      for(int i = 1; i < dataPacketNumElements2; i++){
        dataPacketSnippet2[i] = dataPacketToSend[i-1+offset];
      }
      sendDataViaBLE(dataPacketSnippet2, dataPacketSize2);      
      dataSetCounter = 1;

    }
  }

  return true;
}

bool runfNIRSSequence(void*){

  if (readDataBool){

    int startTimeValue = millis();

    // Set current source state
    setSourceState(sourceNumber);

    // Collect the latest detector data
    updateDetectorValues();
    
    // Update round number
    // 0 to 15 are LEDs
    // 16 is the dark current value
    if (sourceNumber == 32){
      dataReadyToSend = true; // BLE data ready to send
      memcpy(dataPacketToSend, dataPacket, sizeof(dataPacket));
      sourceNumber = 0;
    }
    else{
      sourceNumber++;
    }

  }

  return true;

}

void sendDataViaBLE(int inputArray[], int dataSize){

  if (dataReadyToSend){
    // Send the data
    pDataCharacteristic->setValue((uint8_t*)inputArray, dataSize);
    pDataCharacteristic->notify();
  }

}

void sendDataViaBLE2(int inputArray[], int dataSize){

  // Send the data
  pDataCharacteristic2->setValue((uint8_t*)inputArray, dataSize);
  pDataCharacteristic2->notify();

}

class LEDCallbacks: public BLECharacteristicCallbacks {

    void onWrite(BLECharacteristic *pCharacteristic)
    {
      std::string rxValue = pCharacteristic->getValue();
      if (rxValue.length() > 0) {

        Serial.print(rxValue.length());
        Serial.print("\n\n---------------\nReceived data length: ");
        Serial.print(rxValue.length());
        Serial.print("\n\n---------------\nReceived Values: ");
        Serial.println();
        
        for (int i = 1; i < rxValue.length(); i++){
           
          if (i < NUM_SOURCES){
            Serial.print("LED # ");
            // Serial.print(rxValue[i],HEX);
            int sourceValue = i-1;
            Serial.print(sourceValue);
            Serial.print(", ");
            ledIntensities[sourceValue] = rxValue[i];
            Serial.print(ledIntensities[sourceValue]);
            Serial.print(", ");
            Serial.print(getLEDIntensity(sourceValue));
            Serial.println();
          }

        }

        Serial.println("-- End of received values -- \n\n");

        int code = rxValue[0];
        if (code == 1){
          readDataBool = true;
          Serial.println("readDataBool is true!");
          startTime = millis();
          saveLEDIntensitiesToEEPROM();
          sendBatteryLevel();
        }
        else if (code == 3){
          readDataBool = false;
          Serial.println("readDataBool is false!");
          batteryEventCounter = 5001;
          mcp.setChannelValue(MCP4728_CHANNEL_A, 0);
          saveLEDIntensitiesToEEPROM();
          sendBatteryLevel();
        }        
        else if (code == 5){
          readDataBool = false;
          Serial.println("readDataBool is false! LED Intensities requested");
          sendLEDIntensities();
        }

      }
    }

};


void initBatteryFuelGauge(){

  Wire.begin();

  lipo.enableDebugging(); // Uncomment this line to enable helpful debug messages on Serial

  // Set up the MAX17044 LiPo fuel gauge:
  if (lipo.begin() == false) // Connect to the MAX17044 using the default wire port
  {
    Serial.println(F("MAX17044 not detected. Please check wiring. Freezing."));
    while (1);
  }

	// Quick start restarts the MAX17044 in hopes of getting a more accurate
	// guess for the SOC.
	lipo.quickStart();

	// We can set an interrupt to alert when the battery SoC gets too low.
	// We can alert at anywhere between 1% - 32%:
	lipo.setThreshold(20); // Set alert threshold to 20%.

}


void initSourceDrivers(){

  Wire.begin();

  Serial.println("Initializing LED drivers..");

  initMUX(); // initialize multiplexer

  initDACs(); // initialize DAC

}

void(* resetFunc) (void) = 0; // create a standard reset function

void initBLEServer(){

  // Create the BLE Device
  BLEDevice::init("BBOL NIRDuino (Nano ESP32)");

  // Create the BLE Server
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  // Create the BLE data service
  BLEService *pfNIRSService = pServer->createService(fNIRS_SERVICE_UUID);

  // Create a BLE data characteristic
  pDataCharacteristic = pfNIRSService->createCharacteristic(
                      DATA_CHARACTERISTIC_UUID,
                      BLECharacteristic::PROPERTY_READ   |
                      BLECharacteristic::PROPERTY_WRITE  |
                      BLECharacteristic::PROPERTY_NOTIFY |
                      BLECharacteristic::PROPERTY_INDICATE
                    );

  // Register CCCD on characteristic
  pDataCharacteristic->addDescriptor(new BLE2902());
                    
  // Create a BLE data characteristic
  pDataCharacteristic2 = pfNIRSService->createCharacteristic(
                      DATA_CHARACTERISTIC_UUID2,
                      BLECharacteristic::PROPERTY_READ   |
                      BLECharacteristic::PROPERTY_WRITE  |
                      BLECharacteristic::PROPERTY_NOTIFY |
                      BLECharacteristic::PROPERTY_INDICATE
                    );

  // Register CCCD on characteristic
  pDataCharacteristic2->addDescriptor(new BLE2902());

  // Create a BLE data characteristic
  pLEDCharacteristic = pfNIRSService->createCharacteristic(
                      LED_CHARACTERISTIC_UUID,
                      BLECharacteristic::PROPERTY_READ   |
                      BLECharacteristic::PROPERTY_WRITE  |
                      BLECharacteristic::PROPERTY_NOTIFY |
                      BLECharacteristic::PROPERTY_INDICATE
                    );
                    
  // Register CCCD on characteristic
  pLEDCharacteristic->addDescriptor(new BLE2902());

  // Start the service
  pfNIRSService->start();

  // Set the callback for a characteristic
  pLEDCharacteristic ->setCallbacks(new LEDCallbacks());

  // Start advertising
  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(fNIRS_SERVICE_UUID);
  pAdvertising->setScanResponse(false);
  pAdvertising->setMinPreferred(0x0);  // set value to 0x00 to not advertise this parameter
  BLEDevice::startAdvertising();
  Serial.println("Waiting for connection...");

  // Inform BLE is ready
  digitalWrite(14, HIGH); // Red OFF
  digitalWrite(16, LOW);  // Blue ON
  digitalWrite(15, HIGH); // Green OFF
  
}

void initADCs(){
	Serial.println("*ADS1256 Initialization...."); //Some message
	initialize_ADS1256(CS_pin1, DRDY_pin1, RESET_pin1); //run the initialization function 
	initialize_ADS1256(CS_pin2, DRDY_pin2, RESET_pin2); //run the initialization function 
 
  delay(1000);
	Serial.println("*Initialization finished!"); //Confirmation message
 
	reset_ADS1256(CS_pin1); //Reset the ADS1256
	reset_ADS1256(CS_pin2); //Reset the ADS1256

  delay(1000);
	Serial.println("*Reset finished!"); //Confirmation message

	userDefaultRegisters(CS_pin1, DRDY_pin1); //Set up the default registers
	userDefaultRegisters(CS_pin2, DRDY_pin2); //Set up the default registers

  delay(1000);
	Serial.println("*User default registers set!"); //Confirmation message

}

void updateDetectorValues(){


  // Retrieve the ADC values
  cycleSingleEndedADC(CS_pin1, DRDY_pin1); //the cycleSingleEnded() function cycles through ALL the 8 single ended channels
  cycleSingleEndedADC(CS_pin2, DRDY_pin2); //the cycleSingleEnded() function cycles through ALL the 8 single ended channels


}

void writeRegister(int CS_pin, int DRDY_pin, uint8_t registerAddress, uint8_t registerValueW)
{	
  //Relevant video: https://youtu.be/KQ0nWjM-MtI
   while (digitalRead(DRDY_pin)) {} //we "stuck" here until the DRDY changes its state  
  
	SPI.beginTransaction(SPISettings(1920000, MSBFIRST, SPI_MODE1));
	//SPI_MODE1 = output edge: rising, data capture: falling; clock polarity: 0, clock phase: 1.  

	//CS must stay LOW during the entire sequence [Ref: P34, T24]

  digitalWrite(CS_pin, LOW); //CS_pin goes LOW
  
  delayMicroseconds(5); //see t6 in the datasheet
  
	SPI.transfer(0x50 | registerAddress); // 0x50 = WREG

	SPI.transfer(0x00);	

	SPI.transfer(registerValueW); //we write the value to the above selected register
	
	digitalWrite(CS_pin, HIGH); //CS_pin goes HIGH
	SPI.endTransaction();
}

void reset_ADS1256(int CS_pin)
{
	SPI.beginTransaction(SPISettings(1920000, MSBFIRST, SPI_MODE1)); // initialize SPI with  clock, MSB first, SPI Mode1

	digitalWrite(CS_pin, LOW); //CS_pin goes LOW

	delayMicroseconds(10); //wait

	SPI.transfer(0xFE); //Reset

	delay(2); //Minimum 0.6 ms required for Reset to finish.

	SPI.transfer(0x0F); //Issue SDATAC

	delayMicroseconds(100);

	digitalWrite(CS_pin, HIGH); //CS_pin goes HIGH

	SPI.endTransaction();

  Serial.print("SPI Chip select item: ");
  Serial.print(CS_pin);
  Serial.println(" Reset DONE!"); //confirmation message
}

void initialize_ADS1256(int CS_pin, int DRDY_pin, int RESET_pin)	//starting up the chip by making the necessary steps. This is in the setup() of the Arduino code.
{
	//Setting up the pins first
	//Chip select
	pinMode(CS_pin, OUTPUT); //Chip select is an output
	digitalWrite(CS_pin, LOW); //Chip select LOW

	SPI.begin(); //start SPI (Arduino/STM32 - ADS1256 communication protocol)
  //The STM32-ADS1256 development board uses a different SPI channel (SPI_2)
  //For more info: https://youtu.be/3Rlr0FCffr0

	CS_Value = CS_pin; //We store the value of the CS_pin in a variable

	//DRDY
	pinMode(DRDY_pin, INPUT); //DRDY is an input
	pinMode(RESET_pin, OUTPUT); //RESET pin is an output
	digitalWrite(RESET_pin, LOW); //RESET is set to low 

	delay(500); // Wait

	digitalWrite(RESET_pin, HIGH); //RESET is set to high

	delay(500); // Wait

}

void cycleSingleEndedADC(int CS_pin, int DRDY_pin) //Cycling through all (8) single ended channels
{
  //Relevant video: https://youtu.be/GBWJdyjRIdM
  
  // int cycle = 1;  
 
  for (int cycle = 1; cycle < 9; cycle++)
  {

    registerData = 0;
    SPI.beginTransaction(SPISettings(1920000, MSBFIRST, SPI_MODE1));

    //we cycle through all the 8 single-ended channels with the RDATAC
    //INFO:
    //RDATAC = B00000011
    //SYNC = B11111100
    //WAKEUP = B11111111     
    //---------------------------------------------------------------------------------------------
    /*Some comments regarding the cycling:
    When we start the ADS1256, the preconfiguration already sets the MUX to [AIN0+AINCOM].
    When we start the RDATAC (this function), the default MUX ([AIN0+AINCOM]) will be included in the
    cycling which means that the first readout will be the [AIN0+AINCOM]. But, before we read the data
    from the [AIN0+AINCOM], we have to switch to the next register already, then start RDATA. This is
    demonstrated in Figure 19 on Page 21 of the datasheet. 

    Therefore, in order to get the 8 channels nicely read and formatted, we have to start the cycle
    with the 2nd input of the ADS1256 ([AIN1+AINCOM]) and finish with the first ([AIN0+AINCOM]).

        \ CH1 | CH2 CH3 CH4 CH5 CH6 CH7 CH8 \ CH1 | CH2 CH3 ...

    The switch-case is between the  two '|' characters
    The output (one line of values) is between the two '\' characters. */

    // Assign value to output data array           
    int detector = 1;
    if (CS_pin == CS_pin1){
      detector = 0 + cycle;
    }
    else{
      detector = 8 + cycle;
    }
    
    //Steps are on Page21
    //Step 1. - Updating MUX       
    while (digitalRead(DRDY_pin)) {} //waiting for DRDY

    //Step 2: Write MUX register ----------------------------------------------------------------------
    switch (cycle) 
    {
      //Channels are written manually, so we save time on switching the SPI.beginTransaction on and off.
      case 1: //Channel 2          
          digitalWrite(CS_pin, LOW); //CS must stay LOW during the entire sequence [Ref: P34, T24]
          SPI.transfer(0x50 | 1); // 0x50 = WREG //1 = MUX
          SPI.transfer(0x00); 
          SPI.transfer(B00011000);  //AIN1+AINCOM           
        break;

      case 2: //Channel 3
          digitalWrite(CS_pin, LOW); //CS must stay LOW during the entire sequence [Ref: P34, T24]
          SPI.transfer(0x50 | 1); // 0x50 = WREG //1 = MUX
          SPI.transfer(0x00); 
          SPI.transfer(B00101000);  //AIN2+AINCOM            
        break;

      case 3: //Channel 4
          digitalWrite(CS_pin, LOW); //CS must stay LOW during the entire sequence [Ref: P34, T24]
          SPI.transfer(0x50 | 1); // 0x50 = WREG //1 = MUX
          SPI.transfer(0x00); 
          SPI.transfer(B00111000);  //AIN3+AINCOM            
        break;

      case 4: //Channel 5
          digitalWrite(CS_pin, LOW); //CS must stay LOW during the entire sequence [Ref: P34, T24]
          SPI.transfer(0x50 | 1); // 0x50 = WREG //1 = MUX
          SPI.transfer(0x00); 
          SPI.transfer(B01001000);  //AIN4+AINCOM 
        break;

      case 5: //Channel 6
          digitalWrite(CS_pin, LOW); //CS must stay LOW during the entire sequence [Ref: P34, T24]
          SPI.transfer(0x50 | 1); // 0x50 = WREG //1 = MUX
          SPI.transfer(0x00); 
          SPI.transfer(B01011000);  //AIN5+AINCOM            
        break;

      case 6: //Channel 7
          digitalWrite(CS_pin, LOW); //CS must stay LOW during the entire sequence [Ref: P34, T24]
          SPI.transfer(0x50 | 1); // 0x50 = WREG //1 = MUX
          SPI.transfer(0x00); 
          SPI.transfer(B01101000);  //AIN6+AINCOM            
        break;

      case 7: //Channel 8
          digitalWrite(CS_pin, LOW); //CS must stay LOW during the entire sequence [Ref: P34, T24]
          SPI.transfer(0x50 | 1); // 0x50 = WREG //1 = MUX
          SPI.transfer(0x00); 
          SPI.transfer(B01111000);  //AIN7+AINCOM            
        break;

      case 8: //Channel 1
          digitalWrite(CS_pin, LOW); //CS must stay LOW during the entire sequence [Ref: P34, T24]
          SPI.transfer(0x50 | 1); // 0x50 = WREG //1 = MUX
          SPI.transfer(0x00); 
          SPI.transfer(B00001000); //AIN0+AINCOM              
        break;
    }

    //Step 3: SYNC AND WAKEUP ---------------------------------------------------------------------------     

    //Issue RDATA (0000 0001) command
    SPI.transfer(B11111100); //SYNC

    delayMicroseconds(4); //t11 delay 24*tau = 3.125 us //delay should be larger, so we delay by 4 us
    
    SPI.transfer(B11111111); //WAKEUP

    //Step 4: Issue RDATA (0000 0001) command ---------------------------------------------------------------------------     
    SPI.transfer(B00000001);

    //Wait t6 time (~6.51 us) REF: P34, FIG:30.
    delayMicroseconds(5);

    //step out the data: MSB | mid-byte | LSB,

    //registerData is ZERO
    registerData |= SPI.transfer(0x0F); //MSB comes in, first 8 bit is updated // '|=' compound bitwise OR operator
    registerData <<= 8;         //MSB gets shifted LEFT by 8 bits
    registerData |= SPI.transfer(0x0F); //MSB | Mid-byte
    registerData <<= 8;         //MSB | Mid-byte gets shifted LEFT by 8 bits
    registerData |= SPI.transfer(0x0F); //(MSB | Mid-byte) | LSB - final result
    //After this, DRDY should go HIGH automatically

    // assign data to the correct location in the arraysintervalst
    storeDataInPacket(sourceNumber, detector, registerData);

    registerData = 0; // reset detector data value

    digitalWrite(CS_pin, HIGH); //We finished the command sequence, so we switch it back to HIGH
    
    SPI.endTransaction(); 

  }
  
}

// Function to store 24-bit data, source number, and detector number in the appropriate data packet
void storeDataInPacket(int source, int detector, int32_t data24Bit) {

  // Store data to the required location
  int dataIndex = (source * DETECTORS_PER_SOURCE) + detector -1;
  dataPacket[dataIndex] = data24Bit;

  // Add time stamp after each round
  if (detector == 16){
    dataPacket[dataIndex+1] = millis() - intervalTimeStamp;
    intervalTimeStamp = millis();
  }

}

void userDefaultRegisters(int CS_pin, int DRDY_pin)
{
	// This function is "manually" updating the values of the registers then reads them back.
	// This function should be used in the setup() after performing an initialization-reset process 
  // I use the below listed settings for my "startup configuration"
	/*
		REG   VAL     USE
		0     54      Status Register, Everything Is Default, Except ACAL and BUFEN
		1     1       Multiplexer Register, AIN0 POS, AIN1 POS
		2     0       ADCON, Everything is OFF, PGA = 1
		3     99      DataRate = 50 SPS		
    */	
    
	//We update the 4 registers that we are going to use
  
	delay(500);
  
  writeRegister(CS_pin, DRDY_pin, 0x00, B00110100); //STATUS                         
	delay(200);
	writeRegister(CS_pin, DRDY_pin, 0x01, B00000001); //MUX AIN0+AIN1
	delay(200);
	writeRegister(CS_pin, DRDY_pin, 0x02, B00000000); //ADCON
	delay(200);
	writeRegister(CS_pin, DRDY_pin, 0x03, B11010000); //DRATE 7500sps
	delay(500);
  sendDirectCommand(CS_pin, B11110000);	// SELFCAL
	Serial.println("*Register defaults updated!");

}

void sendDirectCommand(int CS_pin, uint8_t directCommand)
{
	//Direct commands can be found in the datasheet Page 34, Table 24. 
  //Use binary, hex or dec format. 
	//Here, we want to use everything EXCEPT: RDATA, RDATAC, SDATAC, RREG, WREG
	//We don't want to involve DRDY here. We just write, but don't read anything.

	//Start SPI
	SPI.beginTransaction(SPISettings(1920000, MSBFIRST, SPI_MODE1));

	digitalWrite(CS_pin, LOW); //REF: P34: "CS must stay low during the entire command sequence"

	delayMicroseconds(5); //t6 - maybe not necessary

	SPI.transfer(directCommand); //Send Command

	delayMicroseconds(5); //t6 - maybe not necessary

	digitalWrite(CS_pin, HIGH); //REF: P34: "CS must stay low during the entire command sequence"

	SPI.endTransaction();

}

void initMUX(){

    pinMode(s0, OUTPUT); // set the initial mode of the switch pin
    pinMode(s1, OUTPUT); // set the initial mode of the switch pin
    pinMode(s2, OUTPUT); // set the initial mode of the switch pin
    pinMode(s3, OUTPUT); // set the initial mode of the switch pin
    pinMode(MUX_EN_PIN, OUTPUT); // set the initial mode of the MUX pin

}

void initDACs(){
  // Try to initialize!
  if (!mcp.begin(0x60)) {
  // if (!mcp.begin()) {
    Serial.println("Failed to find MCP4728 chip");
    while (1) {
      delay(10);
    }
  }
  else{
    Serial.println("DAC init successful!");
  }
}

void setSourceState(int sourceNumber){
    
  // disable MUX, active low so needs to be set HIGH
  digitalWrite(MUX_EN_PIN, HIGH);

  // Activate relevant MUX pin
  if (sourceNumber == 32){
    
    // enable MUX, active low so needs to be set low
    digitalWrite(MUX_EN_PIN, HIGH);
    
    // set DAC to 0
    mcp.setChannelValue(MCP4728_CHANNEL_A, 0);
    
    // wait for DAC EEPROM 
    
  }
  else{

    // enable MUX, active low so needs to be set low
    digitalWrite(MUX_EN_PIN, LOW);

    int muxPin = 0;
    // set MUX channel
    if (sourceNumber < 16){
      // set mux channel
      muxPin = sourceNumber;
    }
    else{
      muxPin = sourceNumber - 16;
    }

    // DEBUG
    // Serial.print("Source Number: ");
    // Serial.print(sourceNumber);
    // Serial.print(" MuxPin: ");
    // Serial.print(muxPin);
    // Serial.print(" ADC level: ");
    // Serial.print(getLEDIntensity(sourceNumber));
    // Serial.println();

    // set mux pin 
    my_mux.channel(muxPin);

    // set DAC level
    mcp.setChannelValue(MCP4728_CHANNEL_A, getLEDIntensity(sourceNumber));
    // wait for DAC EEPROM writing

    // add some delay to let DAC value stabilize
    delayMicroseconds(500);

  }


}

int getLEDIntensity(int sourceNumber){

    if ((sourceNumber % 2) == 0){ // red

      if (ledIntensities[sourceNumber] == 0){   
        // disable MUX, active low so needs to be set HIGH
        digitalWrite(MUX_EN_PIN, HIGH);
        return 0;
      }
      else{ 
        // enable MUX, active low so needs to be set LOW
        digitalWrite(MUX_EN_PIN, LOW);
        // float voltage = ((ledIntensities[sourceNumber] + 155.0)/53.072)*(4095.0/5.0);
        float voltage = (ledIntensities[sourceNumber]/255.0)*4095.0;
        return (int) voltage;
      }
    }
    else{

      if (ledIntensities[sourceNumber] == 0){
        // disable MUX, active low so needs to be set HIGH
        digitalWrite(MUX_EN_PIN, HIGH);
        return 0;
      }
      else{
        // enable MUX, active low so needs to be set LOW
        digitalWrite(MUX_EN_PIN, LOW);
        // float voltage = ((ledIntensities[sourceNumber] + 177.0)/79.367)*(4095.0/5.0);
        float voltage = (ledIntensities[sourceNumber]/255.0)*4095.0;
        return (int) voltage;
      }

    }
}

void switchOffAllLEDs(){

      Wire.beginTransmission(gpioChipAddr);
      Wire.write(byte(0x12));
      byte pinNumber = 0;
      Wire.write(pinNumber);
      Wire.endTransmission();

      Wire.beginTransmission(gpioChipAddr);
      Wire.write(byte(0x13));
      Wire.write(pinNumber);
      Wire.endTransmission();

}

void getVoltage(int value){
  if (value >> 23 == 1) //if the 24th bit (sign) is 1, the number is negative
  {
    value = value - 16777216;  //conversion for the negative sign
    //"mirroring" around zero
  }
  //This is only valid if PGA = 0 (2^0). Otherwise the voltage has to be divided by 2^(PGA)
  double voltage = ((2 * VREF) / 8388608) * value; //5.0 = Vref; 8388608 = 2^{23} - 1

  Serial.println(voltage, 8); //print it on serial
}

double convertToVoltage2(int32_t registerData)
{
  double outputVoltage = 0;

  if (long minus = registerData >> 23 == 1) //if the 24th bit (sign) is 1, the number is negative
  {
    registerData = registerData - 16777216;  //conversion for the negative sign
    //"mirroring" around zero
  }

  outputVoltage = ((2*VREF) / 8388608)*registerData; //2.5 = Vref; 8388608 = 2^{23} - 1

  //Basically, dividing the positive range with the resolution and multiplying with the bits   
  
  return outputVoltage;
}