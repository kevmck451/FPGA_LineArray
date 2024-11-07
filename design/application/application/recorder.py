# program to run in systemd


import time
import wave
import argparse

import numpy as np

from .hw import HW

def parse_args():
    parser = argparse.ArgumentParser(prog="recorder",
        description="run recorder program for MUAS mic line array")

def recorder():
    print('Recorder Script is Running')

    hw = HW()
    hw.set_use_fake_mics(False)
    hw.set_store_raw_data(True)
    channels = 12
    button_hold_amount = 10
    IDLE = True
    RECORD = True
    file_index = 0
    chunk_num = 0

    while True:

        # IDLE STATE
        print('-' * 20)
        print('idle...')
        # Initiate LED Idle Sequence
        button_counter = 0
        while IDLE:

            # Check Button State / Wait for Press
            if hw.get_button_state():
                button_counter += 1
                if button_counter == button_hold_amount:
                    IDLE = False

            time.sleep(0.1)

        # RECORD STATE
        print('-'*20)
        print('recording setup initiated...')
        time.sleep(1)

        # Get gain value from switches
        print(f'---- Gain Value: {hw.get_gain()}')
        hw.set_gain(hw.get_gain())

        # Filename Logic

        # if file is none: filename = 0_0.wav
        # else: filename = f'{int(file.name.split()[0]) + 1}_{chunk_num}.wav'

        filename = f'{file_index}_{chunk_num}.wav'
        print(f'---- File Name: {filename}')

        wav = wave.open(filename, "wb")
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(hw.mic_freq_hz)

        # create a metadata file with info about recording


        # monitor file size
        # filesize = 0


        # swap buffers at the beginning since the current one probably overflowed
        hw.swap_buffers()

        print("---- Data Capturing")
        button_counter = 0
        while RECORD:
            try:
                data = hw.get_data()

            # record any errors to an error log file
            except ValueError:
                print("oops, probably overflowed")
                continue

            print(f"got {len(data)} samples")
            wav.writeframesraw(np.ascontiguousarray(data[:, :channels]))

            # Initial LED Recording Sequence

            # Check Button State / Wait for Press
            if hw.get_button_state():
                button_counter += 1
                if button_counter == button_hold_amount:
                    RECORD = False

            time.sleep(0.1)


        # End Recording

        wav.close()
        print('---- Recording Successful')


        # LED Flashing Action for Confirmation

        # Start the Loop Over
        file_index += 1
        IDLE = True
        RECORD = True
        time.sleep(2)




if __name__ == "__main__":
    recorder()

