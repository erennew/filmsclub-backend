// import React, { useState, useEffect } from "react";
// import {
//   Dropdown,
//   DropdownTrigger,
//   DropdownMenu,
//   DropdownItem,
// } from "@nextui-org/dropdown";
// import { Avatar, AvatarIcon } from "@nextui-org/avatar";
// import { signOut, onAuthStateChanged } from "firebase/auth";
// import { auth } from "../firebase";
// import { useNavigate } from "react-router-dom";
// import { useAuth } from "../context/AuthContext";
// import PlayerModal from "./PlayerModal";

// import { setDoc, doc, getDoc } from "firebase/firestore";
// import { db } from "../firebase";

// export default function UserInfoBtn() {
//   const [userEmail, setUserEmail] = useState("");
//   const [loading, setLoading] = useState(true);
//   const [isPlayerModalOpen, setIsPlayerModalOpen] = useState(false);
//   const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
//   const [user, setUser] = useState(null);
//   const [userData, setUserData] = useState({});
//   const navigate = useNavigate();
//   const { setIsAuthenticated } = useAuth();

//   useEffect(() => {
//     const unsubscribe = onAuthStateChanged(auth, async (currentUser) => {
//       if (currentUser) {
//         setUserEmail(currentUser.email);
//         setIsAuthenticated(true);
//         setUser(currentUser);

//         const userDoc = await getDoc(doc(db, "Users", currentUser.uid));
//         if (userDoc.exists()) {
//           setUserData(userDoc.data());
//         }

//         // Check if the user has a player set
//         const hasPlayer = Boolean(userDoc.data()?.player);
//         if (!hasPlayer) {
//           setIsPlayerModalOpen(true);
//         }
//       } else {
//         setUserEmail("Not signed in");
//         setIsAuthenticated(false);
//       }
//       setLoading(false);
//     });

//     return () => unsubscribe();
//   }, [setIsAuthenticated]);

//   const handleLogout = async () => {
//     try {
//       await signOut(auth);
//       setIsAuthenticated(false);
//       navigate("/login", { replace: true });
//     } catch (error) {
//       console.error("Logout Error: ", error.message);
//     }
//   };

//   const handlePlayerSubmit = async (player) => {
//     if (user) {
//       await setDoc(doc(db, "Users", user.uid), { player }, { merge: true });
//       console.log(`Player ${player} saved in user data`);
//     }
//     setIsPlayerModalOpen(false);
//   };

//   return (
//     <div className="flex items-center">
//       <Dropdown placement="bottom-end">
//         <DropdownTrigger>
//           <Avatar
//             icon={<AvatarIcon />}
//             classNames={{
//               base: "bg-gradient-to-br from-[#FFB457] to-[#FF705B]",
//               icon: "text-black/80",
//             }}
//           />
//         </DropdownTrigger>
//         <DropdownMenu aria-label="Profile Actions" variant="flat">
//           <DropdownItem
//             key="profile"
//             className="h-14 gap-2"
//             textValue="User profile information"
//           >
//             <p className="font-semibold">Signed in as</p>
//             <p className="font-semibold">
//               {loading ? "Loading..." : userEmail}
//             </p>
//           </DropdownItem>
//           <DropdownItem
//             key="settings"
//             onClick={() => setIsSettingsModalOpen(true)}
//             textValue="My settings"
//           >
//             My Settings
//           </DropdownItem>
//           <DropdownItem
//             isDisabled
//             key="list"
//             textValue="My list is currently unavailable"
//           >
//             My List
//           </DropdownItem>
//           <DropdownItem
//             key="logout"
//             color="danger"
//             onClick={handleLogout}
//             textValue="Log out of the account"
//           >
//             Log Out
//           </DropdownItem>
//         </DropdownMenu>
//       </Dropdown>

//       {/* Player Modal */}
//       <PlayerModal
//         isOpen={isPlayerModalOpen}
//         onClose={() => setIsPlayerModalOpen(false)}
//         onSubmit={handlePlayerSubmit}
//       />

//       {/* Settings Modal */}
//       <PlayerModal
//         isOpen={isSettingsModalOpen}
//         onClose={() => setIsSettingsModalOpen(false)}
//         onSubmit={handlePlayerSubmit}
//         // userData={userData}
//       />
//     </div>
//   );
// }
