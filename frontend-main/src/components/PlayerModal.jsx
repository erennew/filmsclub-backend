import React, { useState } from "react";
import {
  Modal,
  ModalContent,
  ModalHeader,
  ModalBody,
  ModalFooter,
} from "@nextui-org/modal";
import {
  Dropdown,
  DropdownTrigger,
  DropdownMenu,
  DropdownItem,
} from "@nextui-org/dropdown";
import { Button } from "@nextui-org/button";
import { FaRegPlayCircle } from "react-icons/fa"; // MX free
import { IoMdPlayCircle } from "react-icons/io"; // MX paid
import { FcVlc } from "react-icons/fc"; // VLC
import { toast } from "react-toastify"; // Import toast from react-toastify


// Available players and their corresponding icons
const players = ["MX Player (free)", "MX Player (paid)", "VLC Player"];
const playersIcons = [<FaRegPlayCircle />, <IoMdPlayCircle />, <FcVlc />];

const PlayerModal = ({ isOpen, onClose, onSubmit }) => {
  const [selectedPlayer, setSelectedPlayer] = useState("");
  const iconClasses = "text-xl text-default-500 pointer-events-none flex-shrink-0";

  const handlePlayerSubmit = () => {
    const playerToSubmit = selectedPlayer || "MX Player (free)"; // Default to "MX free" if no player selected
    onSubmit(playerToSubmit);
    toast.success(`${playerToSubmit} is Set`);
    onClose();
  };

  const handleClose = () => {
    if (!selectedPlayer || selectedPlayer) {
      onSubmit("MX Player (free)");
      toast.success("Default player is Set")

    }
    onClose();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      backdrop="blur"
      size="sm"
      className="bg-bgColorSecondary/80 text-primaryTextColor"
    >
      <ModalContent>
        <ModalHeader>
          <h3>Select Mobile Player</h3>
        </ModalHeader>
        <ModalBody>
          <Dropdown >
            <DropdownTrigger>
              <Button variant="flat" className="text-white">
                {selectedPlayer || "Select a Player"}
              </Button>
            </DropdownTrigger>
            <DropdownMenu
              aria-label="Player Selection"
              selectionMode="single"
              
              onAction={(key) => setSelectedPlayer(key)}
            >
              {players.map((player, index) => (
                <DropdownItem
                  key={player}
                  startContent={
                    <span className={iconClasses}>{playersIcons[index]}</span>
                  }
                >
                  {player}
                </DropdownItem>
              ))}
            </DropdownMenu>
          </Dropdown>
        </ModalBody>
        <ModalFooter>
          <Button onClick={handlePlayerSubmit}>Submit</Button>
          <Button variant="flat" onClick={handleClose}>
            Close
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
};

export default PlayerModal;
