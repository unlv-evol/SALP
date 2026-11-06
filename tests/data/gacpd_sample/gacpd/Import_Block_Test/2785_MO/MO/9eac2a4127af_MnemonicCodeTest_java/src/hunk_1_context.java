         wordList.remove(0);
     }
 
     public void testBadEntropyLength() throws Exception {
         byte[] entropy = ByteUtils.parseHex("7f7f7f7f7f7f7f7f7f7f7f7f7f7f");
         mc.toMnemonic(entropy);
