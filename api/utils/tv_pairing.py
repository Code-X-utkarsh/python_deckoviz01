"""
Utility for handling TV-Mobile app pairing in the TV app
"""

import json
import asyncio
import requests
import websockets
from typing import Dict, Optional, Any, Union, Callable
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tv_pairing")

class TVPairingClient:
    """
    Client for handling TV-Mobile app pairing in the TV app.
    This handles displaying QR codes and establishing WebSocket connections.
    """
    
    def __init__(self, api_base_url: str, websocket_base_url: str):
        """
        Initialize the TV pairing client.
        
        Args:
            api_base_url (str): Base URL for the API (e.g., "https://example.com")
            websocket_base_url (str): Base URL for WebSockets (e.g., "wss://example.com/ws")
        """
        self.api_base_url = api_base_url.rstrip('/')
        self.websocket_base_url = websocket_base_url.rstrip('/')
        self.device_id = None
        self.room_id = None
        self.qr_code_data = None
        self.ws_connection = None
        
    async def generate_qr_code(self, instructions: str = "Scan to connect your mobile app") -> Dict[str, Any]:
        """
        Generate a QR code for mobile app pairing.
        
        Args:
            instructions (str): Text instructions to display on the QR code
            
        Returns:
            Dict: QR code data including base64 image
        """
        # Prepare the API endpoint
        endpoint = f"{self.api_base_url}/qr/generate-qr"
        
        # Prepare the request payload
        payload = {
            "api_base_url": self.api_base_url,
            "instructions": instructions
        }
        
        try:
            # Make the API call
            response = requests.post(endpoint, json=payload)
            
            if response.status_code == 200:
                data = response.json()
                self.device_id = data.get("device_id")
                self.qr_code_data = data
                logger.info(f"QR code generated for device ID: {self.device_id}")
                return data
            else:
                error_msg = f"Failed to generate QR code: {response.status_code}"
                logger.error(error_msg)
                raise Exception(error_msg)
                
        except requests.RequestException as e:
            logger.error(f"Error connecting to API: {str(e)}")
            raise Exception(f"Connection error: {str(e)}")
    
    async def poll_for_room_id(self, polling_interval: int = 2, max_attempts: int = 150) -> str:
        """
        Poll the server to check if a room ID has been assigned to this device.
        
        Args:
            polling_interval (int): Seconds between polling attempts
            max_attempts (int): Maximum number of polling attempts
            
        Returns:
            str: Room ID if paired, or None if polling timed out
        """
        if not self.device_id:
            raise ValueError("No device ID available. Generate a QR code first.")
            
        endpoint = f"{self.api_base_url}/qr/device/{self.device_id}/room"
        
        attempt = 0
        while attempt < max_attempts:
            try:
                response = requests.get(endpoint)
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get("paired", False):
                        self.room_id = data.get("room_id")
                        logger.info(f"Successfully paired with room ID: {self.room_id}")
                        return self.room_id
                else:
                    logger.warning(f"Error polling for room ID: {response.status_code}")
                    
            except requests.RequestException as e:
                logger.error(f"Connection error while polling: {str(e)}")
                
            # Wait before next attempt
            await asyncio.sleep(polling_interval)
            attempt += 1
            
        logger.warning("Polling for room ID timed out")
        return None
    
    async def connect_websocket(self, 
                                on_connect: Optional[Callable] = None,
                                on_message: Optional[Callable] = None,
                                on_disconnect: Optional[Callable] = None) -> None:
        """
        Connect to the WebSocket server after obtaining a room ID.
        
        Args:
            on_connect: Callback function when connection is established
            on_message: Callback function when a message is received
            on_disconnect: Callback function when connection is closed
        """
        if not self.room_id:
            raise ValueError("No room ID available. Pair with a mobile app first.")
            
        # Build WebSocket URL with room ID
        ws_url = f"{self.websocket_base_url}/tv?room={self.room_id}"
        if self.device_id:
            ws_url += f"&device_id={self.device_id}"
            
        try:
            async with websockets.connect(ws_url) as websocket:
                self.ws_connection = websocket
                logger.info(f"Connected to WebSocket with room ID: {self.room_id}")
                
                # Call the on_connect callback if provided
                if on_connect:
                    await on_connect(websocket)
                
                # Listen for messages
                try:
                    async for message in websocket:
                        try:
                            data = json.loads(message)
                            logger.debug(f"Received message: {data}")
                            
                            # Call the on_message callback if provided
                            if on_message:
                                await on_message(websocket, data)
                                
                        except json.JSONDecodeError:
                            logger.warning(f"Received non-JSON message: {message}")
                except websockets.exceptions.ConnectionClosed:
                    logger.info("WebSocket connection closed")
                    if on_disconnect:
                        await on_disconnect()
                        
        except Exception as e:
            logger.error(f"WebSocket connection error: {str(e)}")
            if on_disconnect:
                await on_disconnect()
    
    async def complete_pairing_flow(self, 
                                   on_qr_generated: Optional[Callable] = None,
                                   on_room_assigned: Optional[Callable] = None,
                                   on_ws_connect: Optional[Callable] = None,
                                   on_ws_message: Optional[Callable] = None,
                                   on_ws_disconnect: Optional[Callable] = None,
                                   custom_instructions: str = "Scan to connect your mobile app") -> None:
        """
        Complete the full pairing flow from QR code generation to WebSocket connection.
        
        Args:
            on_qr_generated: Callback when QR code is generated
            on_room_assigned: Callback when room ID is received
            on_ws_connect: Callback when WebSocket connects
            on_ws_message: Callback when WebSocket message is received
            on_ws_disconnect: Callback when WebSocket disconnects
            custom_instructions: Custom text for the QR code
        """
        try:
            # Step 1: Generate QR code
            qr_data = await self.generate_qr_code(instructions=custom_instructions)
            if on_qr_generated:
                await on_qr_generated(qr_data)
                
            # Step 2: Poll for room ID
            room_id = await self.poll_for_room_id()
            if not room_id:
                logger.error("Failed to receive room ID within the timeout period")
                return
                
            if on_room_assigned:
                await on_room_assigned(room_id)
                
            # Step 3: Connect to WebSocket
            await self.connect_websocket(
                on_connect=on_ws_connect,
                on_message=on_ws_message,
                on_disconnect=on_ws_disconnect
            )
            
        except Exception as e:
            logger.error(f"Error in pairing flow: {str(e)}")
            
    async def send_message(self, message: Union[str, Dict]) -> None:
        """
        Send a message over the WebSocket connection.
        
        Args:
            message: Message to send (string or JSON-serializable dict)
        """
        if not self.ws_connection:
            raise ValueError("No active WebSocket connection")
            
        try:
            if isinstance(message, dict):
                await self.ws_connection.send(json.dumps(message))
            else:
                await self.ws_connection.send(str(message))
                
            logger.debug(f"Sent message: {message}")
            
        except Exception as e:
            logger.error(f"Error sending message: {str(e)}")
            raise
