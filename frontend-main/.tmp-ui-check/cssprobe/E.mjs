import { nextui } from "@nextui-org/theme";
const theme = {
  extend: {
    colors: { ink:"#08090B", carbon:"#14151A", bone:"#F2EDE3", projector:"#E9B872", signal:"#FF5A3C", "projector-bright":"#F0CA98", "projector-deep":"#D9A65C", "bone-muted":"#A8A49B", "bone-subtle":"#8F8B83" },
    fontFamily: { sans: ["Manrope"], display: ["Fraunces"], mono: ["'IBM Plex Mono'"] },
  },
};
export default { content: ["./src/**/*.{js,jsx,ts,tsx}", "./node_modules/@nextui-org/theme/dist/**/*.{js,ts,jsx,tsx}"], theme, darkMode: "class", plugins: [nextui()] };
